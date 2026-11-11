import base64
import json
import time
import uuid

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from conftest import login
from server.db import connect
from test_banking import payment, balances

PIN = '482619'


def setup(client):
    assert login(client).status_code == 200
    assert client.post('/api/security/setup', json={'username':'account.owner','password':'PurpleDemo!2026','pin':PIN}).status_code == 201


def key_pair():
    private = ec.generate_private_key(ec.SECP256R1())
    public = base64.b64encode(private.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
    return private, public


def signed(private, challenge):
    return {'signature':base64.b64encode(private.sign(challenge['message'].encode(),ec.ECDSA(hashes.SHA256()))).decode()}


def enrol(client):
    private,public=key_pair()
    response=client.post('/api/security/biometric/enrolment',json={'password':'PurpleDemo!2026','pin':PIN,'public_key':public})
    assert response.status_code==200,response.text
    challenge=response.json()
    assert client.post('/api/security/biometric/challenges/'+challenge['challenge_id']+'/verify',json=signed(private,challenge)).status_code==200
    return private,public


def medium(client):
    response=client.post('/api/transfers',json=payment(pin=PIN,telemetry={'samples':1}))
    assert response.status_code==200,response.text
    assert response.json()['status']=='step_up_required'
    return response.json()


def start(client,hold):
    response=client.post('/api/step-up/'+hold['challenge_id']+'/biometric',json={})
    assert response.status_code==200,response.text
    return response.json()


def test_username_registration_login_and_no_synthetic_baseline(bank):
    client,app=bank
    body={'email':'new@example.test','username':'New.Owner','password':'StrongPrivate!2026','device_id':'new-device-identifier','full_name':'New Owner','pin':PIN}
    assert client.post('/api/auth/register',json=body).status_code==201
    result=client.post('/api/auth/login',json={'email':'new.owner','password':body['password'],'device_id':body['device_id']})
    assert result.status_code==200
    assert client.get('/api/security/settings').json()['username']=='new.owner'
    response=client.post('/api/transfers',json=payment(pin=PIN))
    assert response.json()['status']=='enrollment_required'
    with connect(app.state.db_path) as db:
        row=db.execute('SELECT * FROM account_security WHERE username=?',('new.owner',)).fetchone()
        assert row['pin_hash']!=PIN and PIN not in row['pin_hash']
        assert 'pin' not in json.dumps(client.get('/api/overview').json()['events'])
    body['email']='other@example.test'
    assert client.post('/api/auth/register',json=body).status_code==409


def test_pin_no_bypass_hash_and_committed_rate_limit(bank):
    client,app=bank;setup(client);before=balances(app)
    for _ in range(4):
        assert client.post('/api/transfers',json=payment(pin='111111')).status_code==403
    assert client.post('/api/transfers',json=payment(pin='111111')).status_code==429
    assert client.post('/api/transfers',json=payment(pin=PIN)).status_code==429
    assert balances(app)==before
    with connect(app.state.db_path) as db:
        assert db.execute("SELECT pin_failures FROM account_security WHERE user_id='demo-irfan'").fetchone()[0]==5
        db.execute("UPDATE account_security SET pin_blocked_until=? WHERE user_id='demo-irfan'",(time.time()-1,))
    assert client.post('/api/transfers',json=payment(pin=PIN)).json()['status']=='completed'
    assert client.post('/api/demo/scenario',json={'scenario':'normal'}).status_code==403
    assert client.post('/api/security/setup',json={'username':'replacement','password':'PurpleDemo!2026','pin':'123456'}).status_code==409


def test_normal_hold_signed_biometric_single_debit_and_high_block(bank):
    client,app=bank;setup(client);private,_=enrol(client)
    assert client.get('/api/security/settings').json()['biometric_enrolled'] is True
    before=balances(app)
    hold=medium(client);assert balances(app)==before
    assert client.post('/api/step-up/'+hold['challenge_id']+'/verify',json={'method':'demo','approved':True}).status_code==403
    challenge=start(client,hold)
    message=json.loads(challenge['message'])
    assert message['amount_minor']==25000 and message['recipient_id']=='demo-sarah'
    url='/api/security/biometric/challenges/'+challenge['challenge_id']+'/verify'
    proof=signed(private,challenge)
    result=client.post(url,json=proof)
    assert result.status_code==200,result.text
    assert result.json()['status']=='completed'
    after=balances(app)
    assert after['demo-irfan']==before['demo-irfan']-25000
    assert after['demo-sarah']==before['demo-sarah']+25000
    assert client.post(url,json=proof).status_code==410
    assert balances(app)==after
    signals=app.state.risk.scenario_features('demo-irfan','block')
    blocked=client.post('/api/transfers',json=payment(pin=PIN,telemetry={**signals,'samples':24})).json()
    assert blocked['status']=='blocked' and blocked['account_status']=='locked'
    assert balances(app)==after


@pytest.mark.parametrize('case',['wrong_key','tamper','replay_other_hold','cancel','expired','other_session','revoked'])
def test_biometric_proofs_fail_closed(bank,case):
    client,app=bank;setup(client);private,_=enrol(client);hold=medium(client);challenge=start(client,hold);before=balances(app)
    url='/api/security/biometric/challenges/'+challenge['challenge_id']+'/verify'
    proof=signed(private,challenge)
    if case=='wrong_key':
        wrong,_=key_pair();proof=signed(wrong,challenge)
    elif case=='tamper':
        proof=signed(private,{**challenge,'message':challenge['message']+' '})
    elif case=='replay_other_hold':
        other=start(client,medium(client));url='/api/security/biometric/challenges/'+other['challenge_id']+'/verify'
    elif case=='cancel':
        assert client.post('/api/step-up/'+hold['challenge_id']+'/cancel',json={}).json()['status']=='cancelled'
    elif case=='expired':
        with connect(app.state.db_path) as db:db.execute('UPDATE biometric_challenges SET expires_at=? WHERE id=?',(time.time()-1,challenge['challenge_id']))
    elif case=='other_session':login(client)
    elif case=='revoked':
        with connect(app.state.db_path) as db:db.execute("DELETE FROM sessions WHERE user_id='demo-irfan'")
    result=client.post(url,json=proof)
    assert result.status_code in (401,403,404,409,410),result.text
    assert balances(app)==before


def test_enrolment_reauth_proof_and_replacement_protection(bank):
    client,app=bank;setup(client);private,public=key_pair()
    body={'password':'incorrect','pin':PIN,'public_key':public}
    assert client.post('/api/security/biometric/enrolment',json=body).status_code==401
    body['password']='PurpleDemo!2026';result=client.post('/api/security/biometric/enrolment',json=body).json()
    assert not client.get('/api/security/settings').json()['biometric_enrolled']
    url='/api/security/biometric/challenges/'+result['challenge_id']+'/verify'
    assert client.post(url,json={'approved':True}).status_code==422
    assert client.post(url,json=signed(private,result)).status_code==200
    assert client.post(url,json=signed(private,result)).status_code==410
    assert client.post('/api/security/biometric/enrolment',json=body).status_code==409


def test_three_bad_signatures_cancel_hold_without_account_lock(bank):
    client,app=bank;setup(client);enrol(client);hold=medium(client);challenge=start(client,hold);wrong,_=key_pair();before=balances(app)
    url='/api/security/biometric/challenges/'+challenge['challenge_id']+'/verify'
    for _ in range(3):assert client.post(url,json=signed(wrong,challenge)).status_code==403
    assert client.post(url,json=signed(wrong,challenge)).status_code==410
    result=client.get('/api/overview').json()
    assert result['user']['status']=='active'
    assert result['transactions'][0]['status']=='cancelled'
    assert balances(app)==before


def test_native_session_header_and_role_boundaries(bank):
    client,_=bank
    token=login(client).json()['token'];client.cookies.clear()
    assert client.get('/api/me',headers={'X-Purple-Session':token}).status_code==200
    assert client.get('/api/me',headers={'X-Purple-Session':'fake'}).status_code==401
    login(client,admin=True)
    assert client.get('/api/security/settings').status_code==403


def test_validation_does_not_echo_secrets(bank):
    client,_=bank;setup(client)
    value='482619-do-not-echo'
    response=client.post('/api/transfers',json=payment(pin=value))
    assert response.status_code==422
    assert value not in response.text and 'input' not in response.text
