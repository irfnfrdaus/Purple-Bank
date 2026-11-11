import math
import json
from pathlib import Path

import pytest

from server.account_pin_rhythm import reference, assess, export_reference, thresholds
from server.pin_rhythm import PinTiming
from server.db import connect
from test_banking import balances
from test_account_security import key_pair, signed


def timing(scale=1.0, gaps=None):
    strokes=[];down=0.
    for i in range(6):
        strokes.append({'down_ms':down,'up_ms':down+110*scale})
        down+=110*scale+(gaps[i]*scale if gaps and i<5 else 200*scale)
    return {'strokes':strokes,'source':'pointer'}


def register(client):
    body={'email':'pin.owner@example.test','username':'pin.owner','password':'PrivatePassword!2026',
          'device_id':'phone-device-identifier','full_name':'PIN Owner','pin':'482619','pin_timing':timing()}
    response=client.post('/api/auth/register',json=body)
    assert response.status_code==201,response.text
    result=client.post('/api/auth/login',json={k:body[k] for k in ('email','password','device_id')})
    assert result.status_code==200
    return result.json()['user']['id'],body


def transfer(client,scale=1.,**extra):
    import uuid
    return client.post('/api/transfers',json={'recipient_id':'demo-sarah','amount_minor':25000,'pin':'482619',
        'request_id':str(uuid.uuid4()),'pin_timing':timing(scale),**extra})


def test_dataset_matches_generator_and_quantiles(tmp_path):
    path=tmp_path/'synthetic.csv';export_reference(path)
    assert path.read_bytes()==Path('data/pin-account-synthetic.csv').read_bytes()
    review,block=thresholds();assert 1.4<math.exp(review)<1.8 and math.exp(block)==pytest.approx(3.)
    profile=reference(PinTiming(**timing()))
    assert assess(PinTiming(**timing()),profile)['tier']=='allow'
    assert assess(PinTiming(**timing(2)),profile)['tier']=='step_up'
    assert assess(PinTiming(**timing(4)),profile)['tier']=='block'
    assert assess(PinTiming(**timing(.2)),profile)['tier']=='block'
    # Equal means do not erase a large change to the sequence of pauses.
    changed=timing(gaps=[20,20,20,20,920])
    result=assess(PinTiming(**changed),profile)
    assert result['tier']!='allow' and result['pin_timing']['cadence_deviation']>0


def test_new_account_same_rhythm_debit_once_and_missing_timing_hold(bank):
    client,app=bank;uid,_=register(client);before=balances(app)
    result=transfer(client);assert result.status_code==200,result.text
    t=result.json()['transaction'];assert t['status']=='completed'
    assert t['decision_details']['decision_source']=='account_pin_rhythm_policy'
    assert t['decision_details']['data_source']=='synthetic_variations_anchored_to_enrolled_pin'
    assert t['risk_score'] is None and t['model_score'] is None
    assert not t['decision_details']['identity_verified']
    assert balances(app)[uid]==before[uid]-25000
    again=client.post('/api/transfers',json={'recipient_id':'demo-sarah','amount_minor':25000,'request_id':t['request_id'],'pin':'482619','pin_timing':timing(4)})
    assert again.json()['replayed'] and again.json()['status']=='completed'
    assert balances(app)[uid]==before[uid]-25000
    held=transfer(client,pin_timing=None)
    assert held.json()['status']=='step_up_required'
    assert balances(app)[uid]==before[uid]-25000
    # Supplying all seven model features cannot bypass the account PIN policy.
    assert transfer(client,pin_timing=None,telemetry={'samples':30,'dwell_ms':100,'flight_ms':200,'swipe_speed':500,'touch_pressure':.5,'accel_std':1,'gyro_std':.1,'tilt_deg':10}).json()['status']=='step_up_required'


def test_slow_medium_requires_real_biometric_then_high_locks(bank):
    client,app=bank;uid,_=register(client)
    private,public=key_pair()
    result=client.post('/api/security/biometric/enrolment',json={'password':'PrivatePassword!2026','pin':'482619','public_key':public}).json()
    assert client.post('/api/security/biometric/challenges/'+result['challenge_id']+'/verify',json=signed(private,result)).status_code==200
    before=balances(app);held=transfer(client,2).json()
    assert held['status']=='step_up_required' and balances(app)==before
    assert client.post('/api/step-up/'+held['challenge_id']+'/verify',json={'method':'demo','approved':True}).status_code==403
    result=client.post('/api/step-up/'+held['challenge_id']+'/biometric',json={}).json()
    proof=signed(private,result);url='/api/security/biometric/challenges/'+result['challenge_id']+'/verify'
    assert client.post(url,json=proof).json()['status']=='completed'
    assert client.post(url,json=proof).status_code==410
    after=balances(app);assert after[uid]==before[uid]-25000
    high=transfer(client,4).json();assert high['status']=='blocked' and high['account_status']=='locked'
    assert balances(app)==after
    assert client.post('/api/step-up/not-a-medium-hold/biometric',json={}).status_code in (401,410)


def test_existing_pin_account_explicit_enrolment_preserves_pin_and_model(bank):
    client,app=bank
    from conftest import login
    from test_account_security import setup
    setup(client)
    with connect(app.state.db_path) as db:old=db.execute("SELECT pin_hash FROM account_security WHERE user_id='demo-irfan'").fetchone()[0]
    assert client.get('/api/security/settings').json()['pin_rhythm_enrolled'] is False
    body={'password':'PurpleDemo!2026','pin':'482619','pin_timing':timing()}
    assert client.post('/api/security/pin-rhythm/enrolment',json={**body,'password':'wrong'}).status_code==401
    assert client.post('/api/security/pin-rhythm/enrolment',json=body).status_code==201
    assert client.get('/api/security/settings').json()['pin_rhythm_enrolled'] is True
    assert client.post('/api/security/pin-rhythm/enrolment',json=body).status_code==409
    with connect(app.state.db_path) as db:
        assert db.execute("SELECT pin_hash FROM account_security WHERE user_id='demo-irfan'").fetchone()[0]==old
        profile=json.loads(db.execute("SELECT reference_json FROM account_pin_rhythm WHERE user_id='demo-irfan'").fetchone()[0])
        assert 'pin' not in profile and profile['mean_flight_ms']==200
    assert 'demo-irfan' in app.state.risk.metrics['users']


def test_invalid_timing_and_wrong_pin_never_move_funds(bank):
    client,app=bank;register(client);before=balances(app)
    assert transfer(client,pin='000000').status_code==403
    broken=timing();broken['strokes'][1]['down_ms']=0
    assert transfer(client,pin_timing=broken).status_code==422
    assert transfer(client,pin_timing={**timing(),'pin':'482619'}).status_code==422
    assert balances(app)==before
