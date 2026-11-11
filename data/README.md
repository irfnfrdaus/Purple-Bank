# Shared synthetic PIN timing reference

`pin-rhythm-synthetic.csv` contains 1,000 computer-generated session summaries, **not measurements of an owner or other participant**. No public biometric dataset was downloaded, and no impostor/person identity labels are supplied.

The exact generator is `server.pin_rhythm.reference_sessions()`: NumPy seed 246810, independent lognormal session means with median dwell110ms/sigma0.23 and median flight200ms/sigma0.25. Row order defines 700 train and 300 validation sessions. Both PIN demo models use this same generator; validation95q/99q sets their thresholds. Only train fits the scaler/model. This file is separate from seven-feature transaction research data and is not accepted as a personal enrolment dataset.

| Column | Meaning |
| --- | --- |
| session_id | Generated row identifier, not a participant ID |
| split | train or validation |
| mean_dwell_ms | Mean key/tap hold duration, milliseconds |
| mean_flight_ms | Mean release-to-next-press gap, milliseconds |
| data_source | Always synthetic_shared_reference |

To reproduce without overwriting the published file:

```bash
.venv/bin/python -m server.pin_rhythm --output .runtime/pin-reference-copy.csv
cmp data/pin-rhythm-synthetic.csv .runtime/pin-reference-copy.csv
```

The exporter refuses to overwrite an existing path. Existing models, databases and participant datasets are untouched. These synthetic rows support a controlled demonstration of timing differences; they do not establish accuracy on real users or identify the person entering a PIN.

## Account PIN synthetic variation template (2.2.1)

`pin-account-synthetic.csv` is a separate deterministic example template (1000dummy variations around110ms/200ms). It calibrates the owner-authorised account PIN timing policy, not the7feature model or the public PIN exercise. No participant/PIN digits are included. See [policy and generation details](../docs/PIN-ACCOUNT-POLICY.md); `server.account_pin_rhythm.export_reference` exclusively creates an exact copy. Actual account timing anchors remain private in SQLite.
