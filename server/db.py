import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password):
    salt = secrets.token_hex(16)
    result = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1)
    return f"{salt}:{result.hex()}"


def verify_password(password, stored):
    salt, expected = stored.split(":")
    result = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1)
    return secrets.compare_digest(result.hex(), expected)


@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def initialise(path):
    with connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, full_name TEXT NOT NULL,
          password_hash TEXT NOT NULL, role TEXT NOT NULL, account_no TEXT UNIQUE NOT NULL,
          balance_minor INTEGER NOT NULL CHECK(balance_minor >= 0),
          status TEXT NOT NULL DEFAULT 'active', demo INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS devices (
          user_id TEXT NOT NULL REFERENCES users(id), device_hash TEXT NOT NULL,
          created_at TEXT NOT NULL, PRIMARY KEY(user_id, device_hash));
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
          device_hash TEXT NOT NULL, expires_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS beneficiaries (
          user_id TEXT REFERENCES users(id), recipient_id TEXT REFERENCES users(id),
          PRIMARY KEY(user_id, recipient_id));
        CREATE TABLE IF NOT EXISTS transfers (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
          recipient_id TEXT NOT NULL REFERENCES users(id), amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
          status TEXT NOT NULL, risk_score REAL NOT NULL, model_score REAL NOT NULL,
          reasons TEXT NOT NULL, request_id TEXT NOT NULL, created_at TEXT NOT NULL,
          UNIQUE(user_id, request_id));
        CREATE TABLE IF NOT EXISTS events (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), kind TEXT NOT NULL,
          severity TEXT NOT NULL, risk_score REAL NOT NULL, model_score REAL NOT NULL,
          model_version TEXT NOT NULL, reasons TEXT NOT NULL, features TEXT NOT NULL,
          transfer_id TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS login_attempts (
          email_hash TEXT NOT NULL, client_hash TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS step_up_challenges (
          id TEXT PRIMARY KEY, transfer_id TEXT UNIQUE NOT NULL REFERENCES transfers(id),
          user_id TEXT NOT NULL REFERENCES users(id), session_hash TEXT NOT NULL,
          expires_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending');
        """)
        # Credentials for normal account login/PIN are additive; legacy accounts
        # and all existing balances/devices/sessions remain untouched.
        db.executescript("""
        CREATE TABLE IF NOT EXISTS account_security (
          user_id TEXT PRIMARY KEY REFERENCES users(id), username TEXT UNIQUE NOT NULL,
          pin_hash TEXT NOT NULL, pin_failures INTEGER NOT NULL DEFAULT 0,
          pin_blocked_until REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
        """)
        db.executescript("""
        CREATE TABLE IF NOT EXISTS biometric_keys (
          user_id TEXT NOT NULL REFERENCES users(id), device_hash TEXT NOT NULL,
          public_key TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(user_id,device_hash));
        CREATE TABLE IF NOT EXISTS biometric_challenges (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
          device_hash TEXT NOT NULL, session_hash TEXT NOT NULL,
          purpose TEXT NOT NULL, transfer_challenge_id TEXT,
          public_key TEXT NOT NULL, message TEXT NOT NULL, expires_at REAL NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending', failures INTEGER NOT NULL DEFAULT 0,
          created_at REAL NOT NULL);
        """)
        db.execute("""CREATE TABLE IF NOT EXISTS account_pin_rhythm (
          user_id TEXT PRIMARY KEY REFERENCES users(id), device_hash TEXT NOT NULL,
          reference_json TEXT NOT NULL, created_at TEXT NOT NULL)""")
        # Additive migrations preserve the existing demo funds and audit history.
        for table in ("transfers", "events"):
            columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            for name, definition in {
                "model_name": "TEXT NOT NULL DEFAULT 'legacy_random_forest'",
                "tier": "TEXT NOT NULL DEFAULT 'legacy'",
                "decision_details": "TEXT NOT NULL DEFAULT '{}'",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        # Named, public demo accounts. This environment is for synthetic funds only.
        for uid, email, name, password, role, number, balance in [
            ("demo-irfan", "irfan@purple.demo", "Irfan Firdaus", "PurpleDemo!2026", "customer", "7001 2000 1122", 1250000),
            ("demo-sarah", "sarah@purple.demo", "Sarah Ahmad", secrets.token_urlsafe(32), "customer", "7001 2000 3344", 800000),
            ("demo-bookshop", "bookshop@purple.demo", "Campus Bookshop", secrets.token_urlsafe(32), "customer", "7001 2000 5566", 0),
            ("demo-admin", "admin@purple.demo", "Security Analyst", "PurpleAdmin!2026", "admin", "ADMIN", 0),
        ]:
            if not db.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
                db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (uid, email, name, password_hash(password), role, number, balance, "active", 1, now()))
        db.execute("INSERT OR IGNORE INTO devices VALUES (?,?,?)", ("demo-irfan", digest("purple-demo-browser"), now()))
        db.execute("INSERT OR IGNORE INTO beneficiaries VALUES (?,?)", ("demo-irfan", "demo-sarah"))
