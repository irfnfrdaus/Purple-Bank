"""Account PIN timing policy anchored to one explicitly enrolled real sample.

Synthetic variation/calibration is a prototype convenience, not genuine
IF/OCSVM enrolment, identity recognition or human accuracy.
The seven-feature models and the public /pin-demo exercise are untouched.
"""
import csv
from functools import lru_cache
from pathlib import Path

import numpy as np
from .pin_rhythm import PinTiming

VERSION = 'account-pin-synthetic-v1'
SOURCE = 'synthetic_variations_anchored_to_enrolled_pin'


def synthetic_sessions(dwell=110.0, flight=200.0):
    rng = np.random.default_rng(20261030)
    return np.column_stack([rng.lognormal(np.log(dwell), .22, 1000), rng.lognormal(np.log(flight), .22, 1000)])


@lru_cache(maxsize=1)
def thresholds():
    # Predeclared genuine-like synthetic variability; no transaction/test data
    # or impostor data is used to select these prototype calibration quantiles.
    samples = synthetic_sessions(1, 1)[700:]
    scores = np.max(np.abs(np.log(samples)), axis=1)
    review, block = map(float, np.quantile(scores, [.95, .99]))
    # A predeclared 3x block floor keeps a broad medium-review band with only
    # one observed enrolment sample; this is a prototype policy, not human FAR.
    return review, max(block, float(np.log(3.0)))


def intervals(timing: PinTiming):
    dwell = [s.up_ms-s.down_ms for s in timing.strokes]
    flight = [b.down_ms-a.up_ms for a,b in zip(timing.strokes,timing.strokes[1:])]
    return np.array(dwell+flight, dtype=float)


def reference(timing: PinTiming):
    return {'version':VERSION, 'intervals_ms':intervals(timing).tolist(),
            'mean_dwell_ms':float(timing.features()[0]),'mean_flight_ms':float(timing.features()[1]),
            'review':thresholds()[0], 'block':thresholds()[1], 'data_source':SOURCE}


def assess(timing: PinTiming, profile):
    measured=timing.features()
    average_drift=np.max(np.abs(np.log(measured / np.array([profile['mean_dwell_ms'],profile['mean_flight_ms']]))))
    cadence_drift=np.sqrt(np.mean(np.log(intervals(timing)/np.array(profile['intervals_ms']))**2))
    score=float(max(average_drift,cadence_drift))
    tier='block' if score>=profile['block'] else 'step_up' if score>=profile['review'] else 'allow'
    return {'tier':tier,'blocked':tier=='block','model_name':VERSION,'model_version':VERSION,
            'model_score':0,'risk_score':0,'score_available':False,'policy_score_available':True,
            'decision_source':'account_pin_rhythm_policy','data_source':SOURCE,
            'input_source':'measured_account_pin_timing','identity_verified':False,
            'reasons':['PIN rhythm checked against registered timing and generated reference limits'],
            'pin_timing':{'deviation':score,'cadence_deviation':float(cadence_drift),
                          'reference_dwell_ms':profile['mean_dwell_ms'],'reference_flight_ms':profile['mean_flight_ms'],
                          'mean_dwell_ms':float(measured[0]),'mean_flight_ms':float(measured[1]),
                          'review':profile['review'],'block':profile['block']}}


def export_reference(path: Path):
    with Path(path).open('x',newline='') as output:
        writer=csv.writer(output,lineterminator='\n')
        writer.writerow(['sample_id','split','mean_dwell_ms','mean_flight_ms','data_source'])
        for i,(dwell,flight) in enumerate(synthetic_sessions()):
            writer.writerow([f'pin-account-{i:04d}','reference' if i<700 else 'calibration',format(dwell,'.17g'),format(flight,'.17g'),'synthetic_example_anchor_110_200'])
