import json
import os
import secrets
import time
import uuid
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictInt

from server.account_pin_rhythm import reference as pin_reference, assess as assess_pin_rhythm
from server.biometric import public_key as biometric_public_key, valid_signature
from server.db import connect, digest, initialise, now, password_hash, verify_password
from server.ml import FEATURES, RiskEngine
from server.pin_rhythm import PinTiming, pin_engine

ROOT = Path(__file__).resolve().parent.parent


class Login(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=8, max_length=200)


class Registration(Login):
    pin_timing: PinTiming | None = None
    full_name: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=12, max_length=128)
    username: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.]{3,32}$")
    pin: str | None = Field(default=None, pattern=r"^[0-9]{6}$")


class SecuritySetup(BaseModel):
    pin_timing: PinTiming | None = None
    username: str = Field(pattern=r"^[A-Za-z0-9_.]{3,32}$")
    password: str = Field(min_length=1, max_length=128, repr=False)
    pin: str = Field(pattern=r"^[0-9]{6}$")


class Telemetry(BaseModel):
    typing_interval_ms: float | None = Field(default=None, ge=10, le=5000)
    key_hold_ms: float | None = Field(default=None, ge=5, le=3000)
    pointer_speed: float | None = Field(default=None, ge=0, le=20000)
    samples: int = Field(default=0, ge=0, le=10000)
    dwell_ms: float | None = Field(default=None, ge=1, le=5000)
    flight_ms: float | None = Field(default=None, gt=0, le=10000)
    swipe_speed: float | None = Field(default=None, gt=0, le=30000)
    touch_pressure: float | None = Field(default=None, ge=0, le=1)
    accel_std: float | None = Field(default=None, ge=0, le=100)
    gyro_std: float | None = Field(default=None, ge=0, le=100)
    tilt_deg: float | None = Field(default=None, ge=-180, le=180)


class Transfer(BaseModel):
    pin_timing: PinTiming | None = None
    recipient_id: str = Field(min_length=1, max_length=80)
    amount_minor: StrictInt = Field(gt=0, le=10000000)
    request_id: str = Field(min_length=8, max_length=100)
    telemetry: Telemetry = Field(default_factory=Telemetry)
    pin: str | None = Field(default=None, pattern=r"^[0-9]{6}$")


class Unlock(BaseModel):
    reason: str = Field(min_length=10, max_length=500)


class Scenario(BaseModel):
    pin: str | None = Field(default=None, pattern=r"^[0-9]{6}$")
    scenario: Literal["normal", "medium", "high"]
    model: Literal["isolation_forest", "one_class_svm"] = "isolation_forest"


class Verification(BaseModel):
    method: Literal["demo"]
    approved: bool


class PinRhythmEnrolment(BaseModel):
    password: str = Field(min_length=1, max_length=128, repr=False)
    pin: str = Field(pattern=r"^[0-9]{6}$")
    pin_timing: PinTiming


class BiometricEnrolment(BaseModel):
    public_key: str = Field(min_length=50, max_length=1000)
    password: str = Field(min_length=1, max_length=128, repr=False)
    pin: str = Field(pattern=r"^[0-9]{6}$")


class BiometricProof(BaseModel):
    signature: str = Field(min_length=16, max_length=500)


class Review(BaseModel):
    approved: bool
    reason: str = Field(min_length=10, max_length=500)


def public_user(row):
    return {key: row[key] for key in ["id", "email", "full_name", "role", "account_no", "balance_minor", "status", "demo"]}


def event(db, uid, kind, risk, features, transfer_id=None, severity="info"):
    db.execute("INSERT INTO events (id,user_id,kind,severity,risk_score,model_score,model_version,reasons,features,transfer_id,created_at,model_name,tier,decision_details) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        str(uuid.uuid4()), uid, kind, severity, risk["risk_score"], risk["model_score"],
        risk["model_version"], json.dumps(risk["reasons"]), json.dumps(features), transfer_id, now(),
        risk.get("model_name", "policy_only"), risk.get("tier", "review"), json.dumps(risk)))


def transfer_dict(row):
    result = dict(row)
    result["reasons"] = json.loads(result["reasons"])
    result["decision_details"] = json.loads(result.get("decision_details", "{}"))
    if not result["decision_details"].get("score_available", False):
        result["decision_details"]["score_available"] = False
        result["risk_score"] = result["model_score"] = None
    return result


def create_app(data_dir=None):
    data_dir = Path(data_dir or os.environ.get("PURPLE_DATA_DIR", ROOT / ".runtime"))
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "purple.sqlite3"

    @asynccontextmanager
    async def lifespan(app):
        initialise(db_path)
        app.state.risk = RiskEngine(data_dir / "ml")
        yield

    app = FastAPI(title="Purple Bank · Behavioural Biometrics FYP", version="3.0.0", lifespan=lifespan)
    app.state.db_path = db_path

    @app.exception_handler(RequestValidationError)
    async def redacted_validation_error(request, error):
        # Pydantic's default response includes submitted values. Never reflect
        # PINs, passwords or proof payloads back in validation/error responses.
        details = [{k:e[k] for k in ("type","loc","msg") if k in e} for e in error.errors()]
        return JSONResponse(status_code=422, content={"detail":details})

    @app.middleware("http")
    async def security_headers(request, call_next):
        # Serve the same application through the scoped public proxy and the
        # existing private root. Never trust a client-supplied prefix header.
        path = request.scope["path"]
        if path == "/purplebank":
            return Response(status_code=308, headers={"Location": "/purplebank/"})
        if path.startswith("/purplebank/"):
            request.scope["root_path"] = "/purplebank"
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return Response("Cross-origin writes are not allowed", status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'"
        if path.removeprefix(request.scope.get("root_path", "")).startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def actor(request: Request):
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else request.headers.get("x-purple-session") or request.cookies.get("purple_session")
        if not token:
            raise HTTPException(401, "Please sign in")
        with connect(db_path) as db:
            row = db.execute("SELECT u.*, s.device_hash, s.token_hash FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token_hash=? AND s.expires_at>?",
                             (digest(token), time.time())).fetchone()
        if not row:
            raise HTTPException(401, "Session expired. Please sign in again")
        return dict(row)

    def customer(user=Depends(actor)):
        if user["role"] != "customer":
            raise HTTPException(403, "Customer account required")
        return user

    def admin(user=Depends(actor)):
        if user["role"] != "admin":
            raise HTTPException(403, "Administrator access required")
        return user

    @app.get("/api/health")
    def health():
        with connect(db_path) as db:
            db.execute("SELECT 1").fetchone()
        return {"status": "ok", "database": "ok", "model": app.state.risk.metrics["version"], "mode": "synthetic-demo" if str(app.state.risk.metrics["data_source"]).lower().startswith("synthetic") else "dataset-research"}

    @app.post("/api/auth/register", status_code=201)
    def register(body: Registration):
        email = body.email.strip().lower()
        if "@" not in email or "." not in email.split("@")[-1]:
            raise HTTPException(422, "Enter a valid email address")
        if (body.username is None) != (body.pin is None):
            raise HTTPException(422, "Username and six-digit PIN are required together")
        with connect(db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            if body.username and db.execute("SELECT 1 FROM account_security WHERE username=?", (body.username.lower(),)).fetchone():
                raise HTTPException(409, "Username is already in use")
            if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                raise HTTPException(409, "An account with that email already exists")
            uid = str(uuid.uuid4())
            number = "70" + str(secrets.randbelow(10**10)).zfill(10)
            db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?,?)", (
                uid, email, body.full_name.strip(), password_hash(body.password), "customer", number, 1000000, "active", 0, now()))
            db.execute("INSERT INTO devices VALUES (?,?,?)", (uid, digest(body.device_id), now()))
            if body.username:
                db.execute("INSERT INTO account_security (user_id,username,pin_hash,created_at) VALUES (?,?,?,?)", (uid,body.username.lower(),password_hash(body.pin),now()))
                if body.pin_timing is not None:
                    db.execute("INSERT INTO account_pin_rhythm VALUES (?,?,?,?)", (uid,digest(body.device_id),json.dumps(pin_reference(body.pin_timing)),now()))
        return {"message": "Simulation account created with RM 10,000 in fictional opening funds. Sign in on this device."}

    @app.post("/api/auth/login")
    def login(body: Login, request: Request, response: Response):
        email = body.email.strip().lower()
        client = digest(request.client.host if request.client else "local")
        stamp = time.time()
        error = None
        token = None
        with connect(db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM login_attempts WHERE created_at<?", (stamp - 600,))
            db.execute("DELETE FROM sessions WHERE expires_at<?", (stamp,))
            count = db.execute("SELECT COUNT(*) FROM login_attempts WHERE (email_hash=? OR client_hash=?) AND created_at>?",
                               (digest(email), client, stamp - 60)).fetchone()[0]
            if count >= 15:
                raise HTTPException(429, "Too many sign-in attempts. Wait one minute")
            db.execute("INSERT INTO login_attempts VALUES (?,?,?)", (digest(email), client, stamp))
            user = db.execute("SELECT u.* FROM users u LEFT JOIN account_security a ON a.user_id=u.id WHERE u.email=? OR a.username=?", (email,email)).fetchone()
            # Perform a real scrypt computation for unknown emails as well.
            valid = verify_password(body.password, user["password_hash"] if user else app.state.dummy_hash)
            if not user or not valid:
                error = HTTPException(401, "Username, email or password is incorrect")
            elif user["status"] == "locked":
                error = HTTPException(423, "Account locked. A security administrator must review it")
            else:
                device_hash = digest(body.device_id)
                known = db.execute("SELECT 1 FROM devices WHERE user_id=? AND device_hash=?", (user["id"], device_hash)).fetchone()
                if user["role"] == "customer" and not known:
                    features = {}
                    risk = {"risk_score": 1, "model_score": 0, "tier": "block", "model_name": "policy_only",
                            "model_version": app.state.risk.metrics["version"], "score_available": False,
                            "decision_source": "device_policy", "reasons": ["Unrecognised device; explicit device policy, not ML inference"]}
                    db.execute("UPDATE users SET status='locked' WHERE id=?", (user["id"],))
                    event(db, user["id"], "unrecognised_device_login", risk, features, severity="critical")
                    error = HTTPException(423, "Unrecognised device. Sign-in blocked and account locked for administrator review")
                else:
                    token = secrets.token_urlsafe(32)
                    db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (digest(token), user["id"], device_hash, stamp + 3600))
        # Raise after commit: risk decisions and rate limiting must not roll back.
        if error:
            raise error
        response.set_cookie("purple_session", token, httponly=True, secure=request.url.scheme == "https",
                            samesite="strict", max_age=3600, path=request.scope.get("root_path") or "/")
        return {"user": public_user(user), "token": token, "expires_in": 3600}

    app.state.dummy_hash = password_hash(secrets.token_urlsafe(32))

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response, user=Depends(actor)):
        with connect(db_path) as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (user["token_hash"],))
        response.delete_cookie("purple_session", path=request.scope.get("root_path") or "/")
        return {"message": "Signed out"}

    @app.get("/api/me")
    def me(user=Depends(actor)):
        return public_user(user)

    @app.get("/api/recipients")
    def recipients(user=Depends(customer)):
        with connect(db_path) as db:
            rows = db.execute("SELECT id, full_name, account_no FROM users WHERE id IN ('demo-sarah','demo-bookshop') AND id!=?", (user["id"],)).fetchall()
        return [dict(row) for row in rows]

    @app.get("/api/security/settings")
    def security_settings(user=Depends(customer)):
        with connect(db_path) as db:
            row = db.execute("SELECT username FROM account_security WHERE user_id=?", (user["id"],)).fetchone()
            bio = db.execute("SELECT 1 FROM biometric_keys WHERE user_id=? AND device_hash=?", (user["id"],user["device_hash"])).fetchone()
            rhythm = db.execute("SELECT 1 FROM account_pin_rhythm WHERE user_id=? AND device_hash=?", (user["id"],user["device_hash"])).fetchone()
        return {"pin_enabled": row is not None, "username": row["username"] if row else None, "biometric_enrolled": bio is not None, "pin_rhythm_enrolled": rhythm is not None}

    @app.post("/api/security/setup", status_code=201)
    def setup_security(body: SecuritySetup, user=Depends(customer)):
        with connect(db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            fresh_session(db,user)
            current = db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            if current["status"] != "active":
                raise HTTPException(423, "Account locked")
            if not verify_password(body.password, current["password_hash"]):
                raise HTTPException(401, "Current password is incorrect")
            if db.execute("SELECT 1 FROM account_security WHERE user_id=?", (user["id"],)).fetchone():
                raise HTTPException(409, "PIN already enrolled. Recovery requires a separate verified process")
            if db.execute("SELECT 1 FROM account_security WHERE username=?", (body.username.lower(),)).fetchone():
                raise HTTPException(409, "Username is already in use")
            db.execute("INSERT INTO account_security (user_id,username,pin_hash,created_at) VALUES (?,?,?,?)", (user["id"], body.username.lower(), password_hash(body.pin), now()))
            if body.pin_timing is not None:
                db.execute("INSERT INTO account_pin_rhythm VALUES (?,?,?,?)", (user["id"],user["device_hash"],json.dumps(pin_reference(body.pin_timing)),now()))
        return {"message": "Username and transaction PIN enrolled"}

    def check_transaction_pin(user, pin):
        error = None
        with connect(db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM account_security WHERE user_id=?", (user["id"],)).fetchone()
            if row is None:
                return  # Existing web/demo accounts retain their prior policy.
            stamp = time.time()
            if row["pin_blocked_until"] > stamp:
                error = HTTPException(429, "PIN temporarily blocked. Wait five minutes")
            elif pin is None or not verify_password(pin, row["pin_hash"]):
                failures = (0 if row["pin_blocked_until"] else row["pin_failures"]) + 1
                blocked = stamp + 300 if failures >= 5 else 0
                db.execute("UPDATE account_security SET pin_failures=?,pin_blocked_until=? WHERE user_id=?", (failures,blocked,user["id"]))
                error = HTTPException(429 if blocked else 403, "PIN temporarily blocked. Wait five minutes" if blocked else "Transaction PIN is incorrect or missing")
            else:
                db.execute("UPDATE account_security SET pin_failures=0,pin_blocked_until=0 WHERE user_id=?", (user["id"],))
        # Raise after commit so failed PIN attempts cannot roll back their counter.
        if error:
            raise error

    @app.post("/api/security/pin-rhythm/enrolment", status_code=201)
    def enrol_pin_rhythm(body: PinRhythmEnrolment, user=Depends(customer)):
        check_transaction_pin(user,body.pin)
        with connect(db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            fresh_session(db,user)
            current=db.execute("SELECT password_hash FROM users WHERE id=?",(user["id"],)).fetchone()
            if not verify_password(body.password,current["password_hash"]):
                raise HTTPException(401,"Current password is incorrect")
            if not db.execute("SELECT 1 FROM account_security WHERE user_id=?",(user["id"],)).fetchone():
                raise HTTPException(409,"Enrol a transaction PIN first")
            if db.execute("SELECT 1 FROM account_pin_rhythm WHERE user_id=?",(user["id"],)).fetchone():
                raise HTTPException(409,"PIN rhythm already enrolled; do not silently replace the baseline")
            db.execute("INSERT INTO account_pin_rhythm VALUES (?,?,?,?)",(user["id"],user["device_hash"],json.dumps(pin_reference(body.pin_timing)),now()))
        return {"message":"PIN rhythm enrolled. Your reference timing is ready for transfers."}

    def active_challenges(user):
        with connect(db_path) as db:
            rows = db.execute("SELECT c.id AS challenge_id,c.transfer_id,c.expires_at,t.amount_minor,t.decision_details FROM step_up_challenges c JOIN transfers t ON t.id=c.transfer_id WHERE c.user_id=? AND c.session_hash=? AND c.state='pending' AND c.expires_at>? AND t.status='step_up_required'", (user["id"],user["token_hash"],time.time())).fetchall()
        return [{**dict(row), "decision_details": json.loads(row["decision_details"])} for row in rows]

    @app.get("/api/overview")
    def overview(user=Depends(customer)):
        with connect(db_path) as db:
            rows = db.execute("SELECT t.*, u.full_name AS recipient_name FROM transfers t JOIN users u ON u.id=t.recipient_id WHERE t.user_id=? ORDER BY t.created_at DESC LIMIT 30", (user["id"],)).fetchall()
            incoming = db.execute("SELECT COALESCE(SUM(amount_minor),0) FROM transfers WHERE recipient_id=? AND status='completed'", (user["id"],)).fetchone()[0]
            outgoing = db.execute("SELECT COALESCE(SUM(amount_minor),0) FROM transfers WHERE user_id=? AND status='completed'", (user["id"],)).fetchone()[0]
            events = db.execute("SELECT * FROM events WHERE user_id=? ORDER BY created_at DESC LIMIT 20", (user["id"],)).fetchall()
        return {"user": public_user(user), "transactions": [transfer_dict(row) for row in rows],
                "incoming_minor": incoming, "outgoing_minor": outgoing,
                "events": [serialize_event(row) for row in events], "model_version": app.state.risk.metrics["version"],
                "pending_challenges": active_challenges(user)}

    def base_hold(tier, reason, source):
        return {"model_score": 0, "risk_score": 0, "score_available": False,
                "tier": tier, "blocked": False, "reasons": [reason], "model_name": "not_scored",
                "model_version": app.state.risk.metrics["version"], "decision_source": source,
                "data_source": app.state.risk.metrics["data_source"]}

    def response_for(db, row, replayed=False):
        result = {"status": row["status"], "transaction": transfer_dict(row), "replayed": replayed,
                  "account_status": db.execute("SELECT status FROM users WHERE id=?", (row["user_id"],)).fetchone()[0]}
        challenge = db.execute("SELECT id,expires_at FROM step_up_challenges WHERE transfer_id=? AND state='pending'", (row["id"],)).fetchone()
        if challenge:
            result.update(challenge_id=challenge["id"], expires_at=challenge["expires_at"])
        return result

    def process_transfer(user, body, simulation=None, model_name=None):
        check_transaction_pin(user, body.pin)
        with connect(db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM sessions WHERE token_hash=? AND user_id=? AND expires_at>?", (user["token_hash"], user["id"], time.time())).fetchone():
                raise HTTPException(401, "Session revoked. Please sign in again")
            existing = db.execute("SELECT * FROM transfers WHERE user_id=? AND request_id=?", (user["id"], body.request_id)).fetchone()
            if existing:
                if existing["recipient_id"] != body.recipient_id or existing["amount_minor"] != body.amount_minor:
                    raise HTTPException(409, "Request ID already used for a different transfer")
                return response_for(db, existing, True)
            current = db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            if current["status"] != "active":
                raise HTTPException(423, "Account locked. Transfers are disabled pending review")
            recipient = db.execute("SELECT * FROM users WHERE id=? AND role='customer'", (body.recipient_id,)).fetchone()
            if not recipient or recipient["id"] == user["id"]:
                raise HTTPException(422, "Choose a valid recipient other than yourself")
            features = {name: getattr(body.telemetry, name) for name in FEATURES if getattr(body.telemetry, name) is not None}
            profile=db.execute("SELECT * FROM account_pin_rhythm WHERE user_id=?",(user["id"],)).fetchone()
            if profile is not None:
                if body.pin_timing is None or profile["device_hash"]!=user["device_hash"]:
                    risk=base_hold("step_up","PIN timing is missing or this device has no timing reference; additional verification required","missing_pin_timing")
                else:
                    risk=assess_pin_rhythm(body.pin_timing,json.loads(profile["reference_json"]))
            elif user["id"] not in app.state.risk.metrics["users"]:
                risk = base_hold("enrollment_required", "No genuine per-user model is enrolled. Import approved enrolment data and retrain before transacting.", "missing_model")
            elif len(features) != len(FEATURES) or body.telemetry.samples < 4:
                risk = base_hold("step_up", "Some required behavioural measurements are missing. Additional verification is required.", "insufficient_telemetry")
                risk["missing_features"] = [name for name in FEATURES if name not in features]
            else:
                risk = app.state.risk.assess(features, user_id=user["id"], model=model_name)
                risk.update(score_available=True, decision_source="model_inference")
            risk.setdefault("input_source", "curated_synthetic_replay" if simulation else "client_reported_summary")
            if simulation:
                risk["scenario"] = simulation
            status = {"allow": "completed", "step_up": "step_up_required", "block": "blocked", "enrollment_required": "enrollment_required"}[risk["tier"]]
            if status in {"completed", "step_up_required"} and body.amount_minor > current["balance_minor"]:
                raise HTTPException(422, "Insufficient simulation funds")
            tid = str(uuid.uuid4())
            db.execute("INSERT INTO transfers (id,user_id,recipient_id,amount_minor,status,risk_score,model_score,reasons,request_id,created_at,model_name,tier,decision_details) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                tid, user["id"], recipient["id"], body.amount_minor, status, risk["risk_score"],
                risk["model_score"], json.dumps(risk["reasons"]), body.request_id, now(), risk["model_name"], risk["tier"], json.dumps(risk)))
            if status == "blocked":
                db.execute("UPDATE users SET status='locked' WHERE id=?", (user["id"],))
            elif status == "completed":
                debit_credit(db, user["id"], recipient["id"], body.amount_minor)
            elif status == "step_up_required":
                db.execute("INSERT INTO step_up_challenges VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), tid, user["id"], user["token_hash"], time.time()+300, "pending"))
            event(db, user["id"], "demo_" + simulation + "_" + status if simulation else "transfer_" + status,
                  risk, features, tid, "critical" if status == "blocked" else "review" if status != "completed" else "info")
            row = db.execute("SELECT * FROM transfers WHERE id=?", (tid,)).fetchone()
            return response_for(db, row)

    def debit_credit(db, uid, recipient, amount):
        changed = db.execute("UPDATE users SET balance_minor=balance_minor-? WHERE id=? AND status='active' AND balance_minor>=?", (amount, uid, amount)).rowcount
        if changed != 1:
            raise HTTPException(409, "Account status or funds changed; no transfer completed")
        db.execute("UPDATE users SET balance_minor=balance_minor+? WHERE id=?", (amount, recipient))
        db.execute("INSERT OR IGNORE INTO beneficiaries VALUES (?,?)", (uid, recipient))

    @app.post("/api/transfers")
    def transfer(body: Transfer, user=Depends(customer)):
        return process_transfer(user, body)

    @app.post("/api/demo/scenario")
    def scenario(body: Scenario, user=Depends(customer)):
        if user["id"] != "demo-irfan":
            raise HTTPException(403, "Curated scenarios are limited to the public demo customer")
        if not str(app.state.risk.metrics["data_source"]).lower().startswith("synthetic"):
            raise HTTPException(409, "Curated scenarios are only available with generated reference artifacts")
        tier = {"normal":"allow", "medium":"step_up", "high":"block"}[body.scenario]
        features = app.state.risk.scenario_features(user["id"], tier, model=body.model)
        intent = Transfer(recipient_id="demo-bookshop" if body.scenario == "high" else "demo-sarah",
                          amount_minor=850000 if body.scenario == "high" else 25000,
                          request_id=str(uuid.uuid4()), telemetry=Telemetry(**features, samples=24), pin=body.pin)
        return process_transfer(user, intent, simulation=body.scenario, model_name=body.model)

    @app.post("/api/demo/attack")
    def simulate_attack(user=Depends(customer)):
        return scenario(Scenario(scenario="high"), user)

    def settle_held(db, transfer, approved, reason, source):
        if transfer["status"] != "step_up_required":
            raise HTTPException(409, "Only a pending medium-risk hold can be reviewed; blocked/enrolment requests cannot be approved")
        challenge = db.execute("SELECT * FROM step_up_challenges WHERE transfer_id=?", (transfer["id"],)).fetchone()
        if not challenge or challenge["state"] != "pending" or challenge["expires_at"] <= time.time():
            raise HTTPException(410, "Verification expired or already used. Submit a fresh request")
        current = db.execute("SELECT status FROM users WHERE id=?", (transfer["user_id"],)).fetchone()
        if current["status"] != "active":
            raise HTTPException(423, "Account locked; this challenge cannot move funds")
        if approved:
            debit_credit(db, transfer["user_id"], transfer["recipient_id"], transfer["amount_minor"])
        final = "completed" if approved else "cancelled"
        detail = json.loads(transfer["decision_details"])
        detail["verification"] = {"method": source, "reason": reason, "approved": approved, "at": now()}
        # Preserve the original ML score and tier. Human/demo review is not a fresh low-risk ML result.
        db.execute("UPDATE transfers SET status=?,decision_details=? WHERE id=?", (final, json.dumps(detail), transfer["id"]))
        db.execute("UPDATE step_up_challenges SET state=? WHERE id=?", (final, challenge["id"]))
        reviewed = {**detail, "reasons": [reason]}
        event(db, transfer["user_id"], "step_up_"+final, reviewed, {}, transfer["id"], severity="review")
        row = db.execute("SELECT * FROM transfers WHERE id=?", (transfer["id"],)).fetchone()
        return response_for(db, row)

    @app.post("/api/step-up/{challenge_id}/verify")
    def verify_step_up(challenge_id: str, body: Verification, user=Depends(customer)):
        with connect(db_path) as db:
            if db.execute("SELECT 1 FROM account_security WHERE user_id=?", (user["id"],)).fetchone():
                raise HTTPException(403, "This account requires real verification or administrator review")
        if user["id"] != "demo-irfan":
            raise HTTPException(403, "Click-to-approve is a simulation only for the public demo account. Real verification is not implemented")
        with connect(db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            challenge = db.execute("SELECT * FROM step_up_challenges WHERE id=? AND user_id=? AND session_hash=?", (challenge_id, user["id"], user["token_hash"])).fetchone()
            if not challenge:
                raise HTTPException(404, "Challenge not found for this session")
            if not db.execute("SELECT 1 FROM sessions WHERE token_hash=? AND expires_at>?", (user["token_hash"], time.time())).fetchone():
                raise HTTPException(401, "Session revoked")
            transfer = db.execute("SELECT * FROM transfers WHERE id=?", (challenge["transfer_id"],)).fetchone()
            return settle_held(db, transfer, body.approved,
                               "Simulated demo verification approved" if body.approved else "Customer cancelled demo verification", "demo_simulation")

    def fresh_session(db, user):
        row = db.execute("SELECT 1 FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.user_id=? AND s.expires_at>? AND u.status='active'", (user["token_hash"],user["id"],time.time())).fetchone()
        if not row:
            raise HTTPException(401, "Session expired, revoked or account locked")

    def new_biometric_challenge(db, user, purpose, key, transfer_challenge=None):
        fresh_session(db, user)
        stamp = time.time()
        count = db.execute("SELECT COUNT(*) FROM biometric_challenges WHERE user_id=? AND created_at>?", (user["id"],stamp-60)).fetchone()[0]
        if count >= 10:
            raise HTTPException(429, "Too many biometric requests. Wait one minute")
        cid = str(uuid.uuid4())
        expires = min(stamp+300,transfer_challenge["expires_at"]) if transfer_challenge else stamp+300
        fields = {"protocol":"purplebank-biometric-v1", "purpose":purpose, "id":cid,
                  "user":user["id"], "device":user["device_hash"], "session":user["token_hash"],
                  "nonce":secrets.token_urlsafe(32), "expires_at":expires}
        if transfer_challenge:
            transfer = db.execute("SELECT * FROM transfers WHERE id=?", (transfer_challenge["transfer_id"],)).fetchone()
            fields.update(transfer_id=transfer["id"], recipient_id=transfer["recipient_id"], amount_minor=transfer["amount_minor"])
        message = json.dumps(fields,sort_keys=True,separators=(",",":"))
        db.execute("INSERT INTO biometric_challenges (id,user_id,device_hash,session_hash,purpose,transfer_challenge_id,public_key,message,expires_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (cid,user["id"],user["device_hash"],user["token_hash"],purpose,transfer_challenge["id"] if transfer_challenge else None,key,message,expires,stamp))
        return {"challenge_id":cid,"message":message,"expires_at":expires}

    @app.post("/api/security/biometric/enrolment")
    def enrol_biometric(body: BiometricEnrolment, user=Depends(customer)):
        check_transaction_pin(user, body.pin)
        try:
            biometric_public_key(body.public_key)
        except (ValueError, TypeError):
            raise HTTPException(422, "Invalid EC P-256 public key")
        with connect(db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            fresh_session(db,user)
            if not db.execute("SELECT 1 FROM account_security WHERE user_id=?", (user["id"],)).fetchone():
                raise HTTPException(409, "Enrol a transaction PIN first")
            current = db.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
            if not verify_password(body.password,current["password_hash"]):
                raise HTTPException(401, "Current password is incorrect")
            if db.execute("SELECT 1 FROM biometric_keys WHERE user_id=? AND device_hash=?", (user["id"],user["device_hash"])).fetchone():
                raise HTTPException(409, "Device key already enrolled; replacement requires verified recovery")
            return new_biometric_challenge(db,user,"enrolment",body.public_key)

    @app.post("/api/step-up/{challenge_id}/biometric")
    def start_biometric(challenge_id: str, user=Depends(customer)):
        with connect(db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            challenge = db.execute("SELECT c.* FROM step_up_challenges c JOIN transfers t ON t.id=c.transfer_id WHERE c.id=? AND c.user_id=? AND c.session_hash=? AND c.state='pending' AND c.expires_at>? AND t.status='step_up_required'", (challenge_id,user["id"],user["token_hash"],time.time())).fetchone()
            if not challenge:
                raise HTTPException(410, "Hold expired or unavailable for this session")
            key = db.execute("SELECT public_key FROM biometric_keys WHERE user_id=? AND device_hash=?", (user["id"],user["device_hash"])).fetchone()
            if not key:
                raise HTTPException(409, "Biometric device key not enrolled. No funds moved")
            return new_biometric_challenge(db,user,"transfer",key["public_key"],challenge)

    @app.post("/api/security/biometric/challenges/{proof_id}/verify")
    def verify_biometric(proof_id: str, body: BiometricProof, user=Depends(customer)):
        error = None
        result = None
        with connect(db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            fresh_session(db,user)
            challenge = db.execute("SELECT * FROM biometric_challenges WHERE id=? AND user_id=? AND device_hash=? AND session_hash=?", (proof_id,user["id"],user["device_hash"],user["token_hash"])).fetchone()
            if not challenge:
                raise HTTPException(404, "Biometric challenge not found for this device/session")
            if challenge["state"] != "pending" or challenge["expires_at"] <= time.time():
                raise HTTPException(410, "Biometric proof expired or already used")
            if not valid_signature(challenge["public_key"],challenge["message"],body.signature):
                failures = challenge["failures"]+1
                db.execute("UPDATE biometric_challenges SET failures=?,state=? WHERE id=?", (failures,"failed" if failures>=3 else "pending",proof_id))
                if failures>=3 and challenge["purpose"]=="transfer":
                    held = db.execute("SELECT t.* FROM transfers t JOIN step_up_challenges c ON c.transfer_id=t.id WHERE c.id=?", (challenge["transfer_challenge_id"],)).fetchone()
                    if held and held["status"]=="step_up_required":
                        settle_held(db,held,False,"Invalid biometric key proof; transfer cancelled","device_biometric_signature")
                error = HTTPException(403,"Invalid biometric signature. No funds moved")
            elif challenge["purpose"]=="enrolment":
                if db.execute("SELECT 1 FROM biometric_keys WHERE user_id=? AND device_hash=?", (user["id"],user["device_hash"])).fetchone():
                    raise HTTPException(409,"Device key already enrolled")
                db.execute("INSERT INTO biometric_keys VALUES (?,?,?,?)", (user["id"],user["device_hash"],challenge["public_key"],now()))
                db.execute("UPDATE biometric_challenges SET state='completed' WHERE id=?", (proof_id,))
                result={"message":"Biometric device key enrolled"}
            else:
                key=db.execute("SELECT public_key FROM biometric_keys WHERE user_id=? AND device_hash=?",(user["id"],user["device_hash"])).fetchone()
                if not key or key["public_key"]!=challenge["public_key"]:
                    raise HTTPException(409,"Enrolled device key is unavailable")
                held=db.execute("SELECT t.* FROM transfers t JOIN step_up_challenges c ON c.transfer_id=t.id WHERE c.id=? AND c.session_hash=?",(challenge["transfer_challenge_id"],user["token_hash"])).fetchone()
                if not held:raise HTTPException(404,"Hold not found")
                result=settle_held(db,held,True,"Enrolled device key signed this session-bound transaction challenge","device_biometric_signature")
                db.execute("UPDATE biometric_challenges SET state='completed' WHERE id=?", (proof_id,))
        if error:raise error
        return result

    @app.post("/api/step-up/{challenge_id}/cancel")
    def cancel_step_up(challenge_id: str, user=Depends(customer)):
        with connect(db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            fresh_session(db,user)
            held=db.execute("SELECT t.* FROM transfers t JOIN step_up_challenges c ON c.transfer_id=t.id WHERE c.id=? AND c.user_id=? AND c.session_hash=?",(challenge_id,user["id"],user["token_hash"])).fetchone()
            if not held:raise HTTPException(404,"Hold not found for this session")
            return settle_held(db,held,False,"Customer cancelled biometric verification","customer_cancel")

    @app.post("/api/admin/transfers/{tid}/review")
    def admin_review(tid: str, body: Review, user=Depends(admin)):
        with connect(db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            transfer = db.execute("SELECT * FROM transfers WHERE id=?", (tid,)).fetchone()
            if not transfer:
                raise HTTPException(404, "Transfer not found")
            return settle_held(db, transfer, body.approved, f"Manual demo review by {user['id']}: {body.reason.strip()}", "admin_manual_review")

    @app.get("/api/model")
    def model(user=Depends(actor)):
        metadata = dict(app.state.risk.metrics)
        # Customers only see their own personal baseline; admins can compare research identities.
        if user["role"] != "admin":
            metadata["users"] = {uid: value for uid,value in metadata["users"].items() if uid == user["id"]}
            if metadata["data_source"] != "synthetic_demo":
                metadata["users"] = {uid: {key: value for key,value in profile.items() if key != "reference_profile"}
                                     for uid,profile in metadata["users"].items()}
        metadata.update(current_user_id=user["id"], score_semantics="Genuine-validation percentile, not fraud probability",
                        report_reference="Interim report sections 3.5, 3.6, 3.8, 3.9; printed pages 14–18")
        return metadata

    def pending_transfers():
        with connect(db_path) as db:
            rows = db.execute("SELECT t.*,u.full_name FROM transfers t JOIN users u ON u.id=t.user_id WHERE t.status IN ('step_up_required','enrollment_required') ORDER BY t.created_at DESC LIMIT 50").fetchall()
        return [transfer_dict(row) for row in rows]

    @app.get("/api/admin/overview")
    def admin_overview(user=Depends(admin)):
        with connect(db_path) as db:
            users = db.execute("SELECT * FROM users WHERE role='customer' ORDER BY status DESC, full_name").fetchall()
            events = db.execute("SELECT e.*, u.full_name FROM events e JOIN users u ON u.id=e.user_id ORDER BY e.created_at DESC LIMIT 100").fetchall()
            blocked = db.execute("SELECT COUNT(*), COALESCE(SUM(amount_minor),0) FROM transfers WHERE status='blocked'").fetchone()
        return {"users": [public_user(row) for row in users], "events": [serialize_event(row) for row in events],
                "blocked_count": blocked[0], "protected_minor": blocked[1], "pending_transfers": pending_transfers()}

    @app.post("/api/admin/users/{uid}/unlock")
    def unlock(uid: str, body: Unlock, user=Depends(admin)):
        with connect(db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            target = db.execute("SELECT * FROM users WHERE id=? AND role='customer'", (uid,)).fetchone()
            if not target:
                raise HTTPException(404, "Customer not found")
            if target["status"] != "locked":
                raise HTTPException(409, "Account is already active")
            db.execute("UPDATE users SET status='active' WHERE id=?", (uid,))
            db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            db.execute("UPDATE step_up_challenges SET state='cancelled' WHERE user_id=? AND state='pending'", (uid,))
            db.execute("UPDATE transfers SET status='cancelled' WHERE user_id=? AND status='step_up_required'", (uid,))
            risk = {"risk_score": 0, "model_score": 0, "score_available": False, "decision_source": "admin_review", "model_version": app.state.risk.metrics["version"],
                    "reasons": [f"Administrator {user['id']} reviewed: {body.reason.strip()}. All customer sessions revoked; trusted devices unchanged."]}
            event(db, uid, "administrator_unlock", risk, {}, severity="review")
        return {"message": "Account unlocked. Previous sessions revoked. Customer must sign in on a trusted device."}

    app.mount("/assets", StaticFiles(directory=ROOT / "web"), name="assets")

    # Public, isolated exercise. Neither endpoint authenticates a customer or
    # writes bank state. Only the separate two-feature synthetic model is used.
    @app.get("/api/demo/pin-rhythm")
    def pin_rhythm_metadata():
        return pin_engine().metadata()

    @app.post("/api/demo/pin-rhythm")
    def pin_rhythm_evaluate(body: PinTiming):
        return pin_engine().evaluate(body)

    def html_page(name, request):
        prefix = request.scope.get("root_path", "")
        html = (ROOT / "web" / name).read_text(encoding="utf-8")
        html = html.replace('href="/', f'href="{prefix}/').replace('src="/', f'src="{prefix}/')
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.get("/pin-demo")
    def pin_rhythm_page(request: Request):
        return html_page("pin-demo.html", request)

    @app.get("/")
    def index(request: Request):
        return html_page("index.html", request)

    return app


def serialize_event(row):
    item = dict(row)
    item["reasons"] = json.loads(item["reasons"])
    item["features"] = json.loads(item["features"])
    item["decision_details"] = json.loads(item.get("decision_details", "{}"))
    if not item["decision_details"].get("score_available", False):
        item["decision_details"]["score_available"] = False
        item["risk_score"] = item["model_score"] = None
    return item


app = create_app()
