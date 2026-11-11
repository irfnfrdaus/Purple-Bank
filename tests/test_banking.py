import concurrent.futures
import uuid

import pytest

from server.db import connect, digest
from server.ml import FEATURES, DEMO_PROFILES, VERSION
from conftest import login


def payment(amount=25000, recipient="demo-sarah", **extra):
    return {"amount_minor": amount, "recipient_id": recipient, "request_id": str(uuid.uuid4()),
            "telemetry": {**dict(zip(FEATURES, DEMO_PROFILES["demo-irfan"])), "samples": 24}, **extra}


def balances(app):
    with connect(app.state.db_path) as db:
        return {row["id"]: row["balance_minor"] for row in db.execute("SELECT id,balance_minor FROM users")}


def test_real_transfer_balances_and_idempotency(bank):
    client, app = bank
    assert login(client).status_code == 200
    before = balances(app)
    body = payment()
    first = client.post("/api/transfers", json=body)
    assert first.status_code == 200
    assert first.json()["transaction"]["status"] == "completed"
    again = client.post("/api/transfers", json=body)
    assert again.json()["replayed"] is True
    assert again.json()["transaction"]["id"] == first.json()["transaction"]["id"]
    after = balances(app)
    assert after["demo-irfan"] == before["demo-irfan"] - 25000
    assert after["demo-sarah"] == before["demo-sarah"] + 25000
    assert sum(before.values()) == sum(after.values())
    body["amount_minor"] = 26000
    assert client.post("/api/transfers", json=body).status_code == 409


def test_suspicious_transfer_preserves_money_and_locks(bank):
    client, app = bank
    login(client)
    before = balances(app)
    result = client.post("/api/demo/attack", json={})
    assert result.status_code == 200
    t = result.json()["transaction"]
    assert t["status"] == "blocked"
    assert t["risk_score"] >= 0.8
    assert balances(app) == before
    assert client.get("/api/me").json()["status"] == "locked"
    assert client.post("/api/transfers", json=payment()).status_code == 423
    event = client.get("/api/overview").json()["events"][0]
    assert event["kind"] == "demo_high_blocked"
    assert set(event["features"]) == set(FEATURES)
    assert event["decision_details"]["input_source"] == "curated_synthetic_replay"
    assert login(client).status_code == 423


def test_new_device_login_is_blocked_and_decision_committed(bank):
    client, app = bank
    result = login(client, device="unknown-attacker-device")
    assert result.status_code == 423
    assert client.get("/api/me").status_code == 401
    with connect(app.state.db_path) as db:
        assert db.execute("SELECT status FROM users WHERE id='demo-irfan'").fetchone()[0] == "locked"
        assert db.execute("SELECT COUNT(*) FROM events WHERE kind='unrecognised_device_login'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM sessions WHERE user_id='demo-irfan'").fetchone()[0] == 0


def test_admin_unlock_revokes_sessions_and_logs_review(bank):
    client, app = bank
    customer_token = login(client).json()["token"]
    client.post("/api/demo/attack", json={})
    assert client.post("/api/admin/users/demo-irfan/unlock", json={"reason": "Identity checked in demo"}).status_code == 403
    assert login(client, admin=True).status_code == 200
    assert client.post("/api/admin/users/demo-irfan/unlock", json={"reason": "ok"}).status_code == 422
    result = client.post("/api/admin/users/demo-irfan/unlock", json={"reason": "Demo identity reviewed by analyst"})
    assert result.status_code == 200
    assert client.get("/api/me", headers={"Authorization": f"Bearer {customer_token}"}).status_code == 401
    overview = client.get("/api/admin/overview").json()
    assert overview["blocked_count"] == 1
    assert overview["events"][0]["kind"] == "administrator_unlock"
    assert login(client).status_code == 200
    assert client.post("/api/transfers", json=payment()).json()["transaction"]["status"] == "completed"


def test_model_can_block_on_known_device_without_policy_override(bank):
    client, app = bank
    login(client)
    before = balances(app)
    # The device is trusted and the amount/payee ordinary. Only behavioural ML blocks.
    signals = app.state.risk.scenario_features("demo-irfan", "block")
    body = payment(25000, telemetry={**signals, "samples": 30})
    result = client.post("/api/transfers", json=body).json()["transaction"]
    assert result["tier"] == "block"
    assert result["decision_details"]["decision_source"] == "model_inference"
    assert result["status"] == "blocked"
    assert balances(app) == before


@pytest.mark.parametrize("amount", [0, -100, 1.5, True, "200", 10000001])
def test_invalid_amount_never_moves_funds(bank, amount):
    client, app = bank
    login(client)
    before = balances(app)
    assert client.post("/api/transfers", json=payment(amount)).status_code == 422
    assert balances(app) == before


def test_funds_and_recipient_checks(bank):
    client, app = bank
    login(client)
    with connect(app.state.db_path) as db:
        db.execute("UPDATE users SET balance_minor=100 WHERE id='demo-irfan'")
    before = balances(app)
    assert client.post("/api/transfers", json=payment(200)).status_code == 422
    assert client.post("/api/transfers", json=payment(recipient="demo-irfan")).status_code == 422
    assert client.post("/api/transfers", json=payment(recipient="does-not-exist")).status_code == 422
    assert balances(app) == before


def test_authentication_authorization_expiry_and_csrf(bank):
    client, app = bank
    assert client.get("/api/overview").status_code == 401
    assert client.post("/api/transfers", json=payment()).status_code == 401
    token = login(client).json()["token"]
    assert client.get("/api/admin/overview").status_code == 403
    assert client.post("/api/transfers", json=payment(), headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/me").headers["cache-control"] == "no-store"
    with connect(app.state.db_path) as db:
        db.execute("UPDATE sessions SET expires_at=0 WHERE token_hash=?", (digest(token),))
    assert client.get("/api/overview").status_code == 401


def test_concurrent_same_request_debits_once(bank):
    client, app = bank
    token = login(client).json()["token"]
    body = payment(5000)
    before = balances(app)
    def send():
        return client.post("/api/transfers", json=body, headers={"Authorization": f"Bearer {token}"})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: send(), range(2)))
    assert all(response.status_code == 200 for response in results)
    assert len({response.json()["transaction"]["id"] for response in results}) == 1
    assert balances(app)["demo-irfan"] == before["demo-irfan"] - 5000


def test_registration_trusts_only_enrolled_device(bank):
    client, app = bank
    account = {"email": "researcher@example.test", "full_name": "Research Student",
               "password": "FictionalResearch!2026", "device_id": "new-research-device"}
    assert client.post("/api/auth/register", json=account).status_code == 201
    assert client.post("/api/auth/register", json=account).status_code == 409
    assert client.post("/api/auth/login", json=account).status_code == 200
    assert client.post("/api/demo/attack", json={}).status_code == 403
    assert client.post("/api/auth/login", json={**account, "device_id": "different-new-device"}).status_code == 423


def test_login_rate_limit_persists_after_failures(bank):
    client, app = bank
    for _ in range(15):
        result = client.post("/api/auth/login", json={"email": "missing@example.test", "password": "wrong", "device_id": "rate-limit-device"})
        assert result.status_code == 401
    assert login(client).status_code == 429


def test_web_assets_health_and_metrics(bank):
    client, app = bank
    assert client.get("/").status_code == 200
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/api/health").json()["model"] == VERSION
    assert client.get("/api/model").status_code == 401
    login(client)
    metrics = client.get("/api/model").json()
    assert set(metrics["users"]) == {"demo-irfan"}
    assert set(metrics["users"]["demo-irfan"]["models"]) == {"isolation_forest", "one_class_svm"}
    assert metrics["users"]["demo-irfan"]["counts"]["train_genuine"] == 300
    assert "synthetic" in metrics["data_source"]


def test_medium_hold_requires_verification_then_debits_once(bank):
    client, app = bank
    login(client)
    before = balances(app)
    result = client.post('/api/demo/scenario', json={'scenario':'medium','model':'one_class_svm'}).json()
    assert result['status'] == 'step_up_required'
    assert result['transaction']['tier'] == 'step_up'
    assert balances(app) == before
    overview = client.get('/api/overview').json()
    assert overview['pending_challenges'][0]['challenge_id'] == result['challenge_id']
    endpoint = f"/api/step-up/{result['challenge_id']}/verify"
    approved = client.post(endpoint, json={'method':'demo','approved':True})
    assert approved.status_code == 200
    assert approved.json()['status'] == 'completed'
    assert approved.json()['transaction']['tier'] == 'step_up'
    assert approved.json()['transaction']['decision_details']['verification']['method'] == 'demo_simulation'
    assert balances(app)['demo-irfan'] == before['demo-irfan'] - 25000
    assert client.post(endpoint, json={'method':'demo','approved':True}).status_code == 409
    assert balances(app)['demo-irfan'] == before['demo-irfan'] - 25000


def test_missing_modalities_hold_without_invented_score(bank):
    client, app = bank
    login(client)
    before = balances(app)
    result = client.post('/api/transfers', json=payment(telemetry={'samples':4,'typing_interval_ms':180})).json()
    assert result['status'] == 'step_up_required'
    detail=result['transaction']['decision_details']
    assert detail['score_available'] is False and detail['decision_source']=='insufficient_telemetry'
    assert result['transaction']['model_score'] is None and result['transaction']['risk_score'] is None
    assert len(detail['missing_features']) == len(FEATURES)
    assert balances(app)==before


def test_stepup_challenge_bound_to_session_and_expires(bank):
    client, app = bank
    token1=login(client).json()['token']
    result=client.post('/api/demo/scenario', json={'scenario':'medium'}).json()
    login(client)
    endpoint=f"/api/step-up/{result['challenge_id']}/verify"
    assert client.post(endpoint,json={'method':'demo','approved':True}).status_code==404
    before=balances(app)
    with connect(app.state.db_path) as db:
        db.execute('UPDATE step_up_challenges SET expires_at=0 WHERE id=?',(result['challenge_id'],))
    assert client.post(endpoint,json={'method':'demo','approved':True},headers={'Authorization':f'Bearer {token1}'}).status_code==410
    assert balances(app)==before


def test_stepup_cancel_and_later_lock_never_move_funds(bank):
    client, app=bank
    login(client)
    before=balances(app)
    result=client.post('/api/demo/scenario',json={'scenario':'medium'}).json()
    endpoint=f"/api/step-up/{result['challenge_id']}/verify"
    assert client.post(endpoint,json={'method':'demo','approved':False}).json()['status']=='cancelled'
    assert balances(app)==before
    result=client.post('/api/demo/scenario',json={'scenario':'medium'}).json()
    endpoint=f"/api/step-up/{result['challenge_id']}/verify"
    client.post('/api/demo/attack',json={})
    assert client.post(endpoint,json={'method':'demo','approved':True}).status_code==423
    assert balances(app)==before


def test_unenrolled_account_cannot_bypass_with_demo_or_admin(bank):
    client, app=bank
    account={'email':'coldstart@example.test','password':'UniqueFictional!2026','full_name':'Cold Start','device_id':'enrolled-browser-id'}
    assert client.post('/api/auth/register',json=account).status_code==201
    assert client.post('/api/auth/login',json=account).status_code==200
    before=balances(app)
    result=client.post('/api/transfers',json=payment()).json()
    assert result['status']=='enrollment_required'
    assert result['transaction']['decision_details']['score_available'] is False
    assert 'challenge_id' not in result
    assert client.post('/api/demo/scenario',json={'scenario':'normal'}).status_code==403
    assert client.post('/api/step-up/fake-challenge/verify',json={'method':'demo','approved':True}).status_code==403
    login(client,admin=True)
    assert client.post(f"/api/admin/transfers/{result['transaction']['id']}/review",json={'approved':True,'reason':'Manual review cannot invent a model'}).status_code==409
    assert balances(app)==before


def test_admin_manual_review_and_denial(bank):
    client,app=bank
    login(client)
    before=balances(app)
    result=client.post('/api/demo/scenario',json={'scenario':'medium'}).json()
    endpoint=f"/api/admin/transfers/{result['transaction']['id']}/review"
    assert client.post(endpoint,json={'approved':True,'reason':'Demo manual review'}).status_code==403
    login(client,admin=True)
    assert client.get('/api/admin/overview').json()['pending_transfers'][0]['id']==result['transaction']['id']
    response=client.post(endpoint,json={'approved':True,'reason':'Supervisor demo manual review'})
    assert response.status_code==200 and response.json()['status']=='completed'
    assert balances(app)['demo-irfan']==before['demo-irfan']-25000
    assert client.post(endpoint,json={'approved':True,'reason':'Attempt repeated review'}).status_code==409


def test_concurrent_stepup_approval_debits_only_once(bank):
    client,app=bank
    token=login(client).json()['token']
    result=client.post('/api/demo/scenario',json={'scenario':'medium'}).json()
    before=balances(app)
    def approve(_):
        return client.post(f"/api/step-up/{result['challenge_id']}/verify",json={'method':'demo','approved':True},headers={'Authorization':f'Bearer {token}'})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(approve,range(2)))
    assert sorted(r.status_code for r in results)==[200,409]
    assert balances(app)['demo-irfan']==before['demo-irfan']-25000
    assert sum(balances(app).values())==sum(before.values())


def test_both_models_drive_all_three_tiers(bank):
    client,app=bank
    login(client)
    for model in ['isolation_forest','one_class_svm']:
        low=client.post('/api/demo/scenario',json={'scenario':'normal','model':model}).json()
        assert low['status']=='completed'
        assert low['transaction']['model_name']==model
        mid=client.post('/api/demo/scenario',json={'scenario':'medium','model':model}).json()
        assert mid['status']=='step_up_required'
        client.post(f"/api/step-up/{mid['challenge_id']}/verify",json={'method':'demo','approved':False})
        high=client.post('/api/demo/scenario',json={'scenario':'high','model':model}).json()
        assert high['status']=='blocked'
        login(client,admin=True)
        client.post('/api/admin/users/demo-irfan/unlock',json={'reason':'Demo scenario reviewed for next model'})
        login(client)
