"""Per-user one-class behavioural models; synthetic demo until a licensed CSV is supplied.

Only genuine training sessions fit models/scalers. Genuine validation sessions calibrate
thresholds. Cross-user impostors and held-out genuine sessions are evaluation-only.
"""
import argparse
import csv
import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.metrics import confusion_matrix, roc_auc_score, roc_curve
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import OneClassSVM

FEATURES = ["dwell_ms", "flight_ms", "swipe_speed", "touch_pressure", "accel_std", "gyro_std", "tilt_deg"]
VERSION = "purple-oneclass-v2"
MODEL_NAMES = {"isolation_forest": "Isolation Forest", "one_class_svm": "One-Class SVM"}
DEFAULT_MODEL = "isolation_forest"
REVIEW_QUANTILE = 0.95
BLOCK_QUANTILE = 0.99
# Synthetic centres are illustrative profiles, not measured people.
DEMO_PROFILES = {
    "demo-irfan": [95, 85, 650, .45, .13, .09, 12],
    "demo-sarah": [119, 128, 505, .61, .20, .14, 25],
    "demo-bookshop": [78, 155, 850, .31, .10, .055, -3],
}
CSV_FIELDS = ["user_id", "session_id", "split", "label", *FEATURES]
LIMITATIONS = [
    "Synthetic demo data is not evidence of real-user identity or hacker detection accuracy.",
    "Client-reported behaviour can be spoofed. No hardware attestation or identity guarantee.",
    "Scores are anomaly scores/validation percentiles, not calibrated fraud probabilities.",
    "Public behavioural dataset not supplied; import requires licence and provenance review.",
    "Default serving model is fixed Isolation Forest; held-out tests compare both models, not tune thresholds.",
]


def synthetic_data(seed=42):
    """One session summary per row, with disjoint train/validation/test session IDs."""
    rng = np.random.default_rng(seed)
    genuine = {}
    widths = np.array([14, 24, 135, .065, .038, .025, 6.5])
    rows = []
    for uid, centre in DEMO_PROFILES.items():
        genuine[uid] = []
        for split, count in [("train", 300), ("validation", 120), ("test", 120)]:
            for i in range(count):
                latent = rng.normal()
                value = np.array(centre) + widths * (rng.normal(size=len(FEATURES)) + .2 * latent)
                value[:3] = np.maximum(value[:3], [15, 10, 30])
                value[3] = np.clip(value[3], .01, 1)
                value[4:6] = np.maximum(value[4:6], .002)
                row = {"user_id": uid, "session_id": f"{uid}:{split}:{i}", "split": split,
                       "label": "genuine", **dict(zip(FEATURES, value.tolist()))}
                rows.append(row)
                if split == "test":
                    genuine[uid].append(row)
    # Use only another identity's held-out test sessions as impostor comparisons.
    for target in DEMO_PROFILES:
        for source, sessions in genuine.items():
            if source != target:
                for row in sessions:
                    rows.append({**row, "user_id": target, "label": "impostor"})
    return rows


def read_dataset(path):
    with Path(path).open(newline="") as stream:
        reader = csv.DictReader(stream)
        missing = set(CSV_FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing CSV columns: {', '.join(sorted(missing))}")
        rows = list(reader)
    return validate_dataset(rows)


def validate_dataset(rows):
    if not rows:
        raise ValueError("Dataset is empty")
    clean, seen, session_splits = [], set(), {}
    for index, row in enumerate(rows, start=2):
        try:
            uid, sid = str(row["user_id"]).strip(), str(row["session_id"]).strip()
            split, label = row["split"], row["label"]
            values = np.array([float(row[name]) for name in FEATURES])
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError(f"Invalid feature row {index}") from error
        if not uid or len(uid) > 100 or not sid or len(sid) > 200:
            raise ValueError(f"Invalid user/session identifier at row {index}")
        if split not in {"train", "validation", "test"} or label not in {"genuine", "impostor"}:
            raise ValueError(f"Invalid split or label at row {index}")
        if split != "test" and label != "genuine":
            raise ValueError("Impostor sessions are evaluation-only, never training or calibration")
        if not np.isfinite(values).all():
            raise ValueError(f"Non-finite feature at row {index}")
        if np.any(values[:3] <= 0) or not 0 <= values[3] <= 1 or np.any(values[4:6] < 0) or abs(values[6]) > 180:
            raise ValueError(f"Feature outside physical schema bounds at row {index}")
        # A source session may be reused against a different target only in the same
        # split (cross-user evaluation). It must never cross a split boundary.
        if sid in session_splits and session_splits[sid] != split:
            raise ValueError("Session leakage across train/validation/test splits")
        session_splits[sid] = split
        if (uid, sid) in seen:
            raise ValueError("Duplicate user/session summary or session leakage")
        seen.add((uid, sid))
        clean.append({"user_id": uid, "session_id": sid, "split": split, "label": label,
                      **dict(zip(FEATURES, values.tolist()))})
    for uid in {row["user_id"] for row in clean}:
        user_rows = [row for row in clean if row["user_id"] == uid]
        required = [("train", "genuine", 50), ("validation", "genuine", 30),
                    ("test", "genuine", 20), ("test", "impostor", 20)]
        for split, label, minimum in required:
            count = sum(row["split"] == split and row["label"] == label for row in user_rows)
            if count < minimum:
                raise ValueError(f"{uid}: need at least {minimum} {split}/{label} sessions")
    return clean


def raw_scores(pipeline, values):
    # Invert both APIs so larger always means more anomalous.
    transformed = pipeline[0].transform(values)
    estimator = pipeline[1]
    if isinstance(estimator, IsolationForest):
        return -estimator.score_samples(transformed)
    return -estimator.decision_function(transformed)


def equal_error_rate(labels, scores):
    """Interpolated FAR=FRR crossing; label 1 is an impostor/anomaly."""
    false_reject, true_detect, _ = roc_curve(labels, scores, drop_intermediate=False)
    false_accept = 1 - true_detect
    difference = false_reject - false_accept
    right = int(np.flatnonzero(difference >= 0)[0])
    if right == 0:
        return float((false_reject[0] + false_accept[0]) / 2)
    left = right - 1
    weight = -difference[left] / (difference[right] - difference[left])
    return float(false_reject[left] + weight * (false_reject[right] - false_reject[left]))


def evaluate(labels, scores, thresholds):
    # A step-up challenge is NOT an automatic acceptance. Evaluation uses the
    # review threshold to classify sessions requiring intervention as suspicious.
    predicted = (scores >= thresholds["review"]).astype(int)
    matrix = confusion_matrix(labels, predicted, labels=[0, 1])
    tn, fp, fn, tp = matrix.ravel()
    return {"roc_auc": float(roc_auc_score(labels, scores)), "eer": equal_error_rate(labels, scores),
            "far": float(fn / (fn + tp)), "frr": float(fp / (fp + tn)),
            "accuracy": float(np.mean(predicted == labels)), "confusion_matrix": matrix.tolist(),
            "thresholds": thresholds, "evaluation_threshold": "review",
            "evaluation_note": "Step-up and block count as flagged; only low risk is automatic acceptance.",
            "test_rows": len(labels)}


def _assessment(bundle, values, model_name):
    model = bundle["models"][model_name]
    score = float(raw_scores(model["pipeline"], np.array([values]))[0])
    thresholds = model["thresholds"]
    tier = "block" if score >= thresholds["block"] else "step_up" if score >= thresholds["review"] else "allow"
    percentile = float(np.searchsorted(model["validation_scores"], score, side="right") / len(model["validation_scores"]))
    return {"model_score": round(score, 6), "risk_score": round(percentile, 4), "tier": tier,
            "blocked": tier == "block", "score_available": True, "model_name": model_name,
            "model_label": MODEL_NAMES[model_name], "raw_thresholds": thresholds}


def _scenario_candidates(bundle, rng, model_name=DEFAULT_MODEL):
    reference = np.array(bundle["reference_profile"])
    spread = bundle["feature_std"]
    # Select examples by the served model's calibrated boundaries; these are
    # curated synthetic demonstrations, never included in reported evaluation.
    candidates = [reference]
    for factor in np.linspace(0, 12, 160):
        for _ in range(12):
            vector = reference + spread * rng.normal(size=len(FEATURES)) * factor
            vector[:3] = np.maximum(vector[:3], [15, 10, 30])
            vector[3] = np.clip(vector[3], .01, 1)
            vector[4:6] = np.maximum(vector[4:6], .002)
            vector[6] = np.clip(vector[6], -180, 180)
            candidates.append(vector)
    array = np.asarray(candidates)
    raw = raw_scores(bundle["models"][model_name]["pipeline"], array)
    thresholds = bundle["models"][model_name]["thresholds"]
    conditions = {"allow": raw < thresholds["review"],
                  "step_up": (raw >= thresholds["review"]) & (raw < thresholds["block"]),
                  "block": raw >= thresholds["block"]}
    chosen = {}
    for tier, condition in conditions.items():
        indexes = np.flatnonzero(condition)
        if not len(indexes):
            raise ValueError(f"Cannot construct a {tier} demonstration for this profile")
        # Pick a comfortably interior score; extreme block examples show clearly.
        selected = indexes[len(indexes) // 2] if tier != "block" else indexes[np.argmax(raw[indexes])]
        chosen[tier] = dict(zip(FEATURES, array[selected].tolist()))
    return chosen


def train(output: Path, data: Path | None = None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rows = read_dataset(data) if data else validate_dataset(synthetic_data())
    data_source = "user_supplied_csv" if data else "synthetic_demo"
    report = {"version": VERSION, "data_source": data_source,
              "data_description": "User-supplied behavioural CSV; provenance/licence must be reviewed" if data else "Generated behavioural sessions, not measurements from real participants",
              "seed": 42, "features": FEATURES, "model_names": MODEL_NAMES,
              "default_model": DEFAULT_MODEL, "users": {},
              "calibration": {"source": "genuine validation sessions only", "review_quantile": REVIEW_QUANTILE, "block_quantile": BLOCK_QUANTILE},
              "score_meaning": "Empirical genuine-validation anomaly percentile, not probability of fraud",
              "limitations": LIMITATIONS,
              "csv_schema": CSV_FIELDS}
    users = {}
    for uid in sorted({row["user_id"] for row in rows}):
        selected = [row for row in rows if row["user_id"] == uid]
        training = [row for row in selected if row["split"] == "train"]
        validation = [row for row in selected if row["split"] == "validation"]
        testing = [row for row in selected if row["split"] == "test"]
        x_train = np.array([[row[name] for name in FEATURES] for row in training])
        x_validation = np.array([[row[name] for name in FEATURES] for row in validation])
        x_test = np.array([[row[name] for name in FEATURES] for row in testing])
        labels = np.array([int(row["label"] == "impostor") for row in testing])
        reference = np.mean(x_train, axis=0)
        bundle = {"reference_profile": reference, "feature_std": np.maximum(np.std(x_train, axis=0), 1e-9), "models": {}}
        user_report = {"reference_profile": dict(zip(FEATURES, reference.tolist())),
                       "counts": {"train_genuine": len(training), "validation_genuine": len(validation),
                                  "test_genuine": int(np.sum(labels == 0)), "test_impostor": int(np.sum(labels == 1))},
                       "models": {}}
        estimators = {"isolation_forest": IsolationForest(n_estimators=160, max_samples="auto", contamination="auto", random_state=42, n_jobs=2),
                      "one_class_svm": OneClassSVM(kernel="rbf", gamma="scale", nu=.05)}
        for name, estimator in estimators.items():
            pipeline = make_pipeline(StandardScaler(), estimator).fit(x_train)
            validation_scores = np.sort(raw_scores(pipeline, x_validation))
            thresholds = {"review": float(np.quantile(validation_scores, REVIEW_QUANTILE)),
                          "block": float(np.quantile(validation_scores, BLOCK_QUANTILE))}
            if thresholds["block"] <= thresholds["review"]:
                raise ValueError(f"{uid}: validation data cannot separate review and block thresholds")
            bundle["models"][name] = {"pipeline": pipeline, "thresholds": thresholds,
                                      "validation_scores": validation_scores}
            user_report["models"][name] = evaluate(labels, raw_scores(pipeline, x_test), thresholds)
        # Curated scenarios only for generated data, never genuine imported participants.
        if not data:
            bundle["scenarios"] = {name: _scenario_candidates(bundle, np.random.default_rng(43), name) for name in MODEL_NAMES}
        users[uid] = bundle
        report["users"][uid] = user_report
    report["train_rows"] = sum(user["counts"]["train_genuine"] for user in report["users"].values())
    report["validation_rows"] = sum(user["counts"]["validation_genuine"] for user in report["users"].values())
    report["test_rows"] = sum(user["counts"]["test_genuine"] + user["counts"]["test_impostor"] for user in report["users"].values())
    # Local artifacts only: never deserialize an untrusted joblib upload.
    joblib.dump({"version": VERSION, "features": FEATURES, "users": users, "data_source": data_source}, output / "model.joblib")
    (output / "metrics.json").write_text(json.dumps(report, indent=2))
    with (output / "training_data.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return report


class RiskEngine:
    def __init__(self, output):
        output = Path(output)
        artifact = joblib.load(output / "model.joblib")
        if artifact.get("version") != VERSION or artifact.get("features") != FEATURES:
            raise ValueError("Outdated model artifact: retrain with python -m server.ml")
        self.users = artifact["users"]
        self.metrics = json.loads((output / "metrics.json").read_text())
        self.data_source = artifact["data_source"]

    def reference_profile(self, user_id="demo-irfan"):
        if user_id not in self.users:
            return None
        return dict(zip(FEATURES, self.users[user_id]["reference_profile"].tolist()))

    def scenario_features(self, user_id="demo-irfan", tier="allow", model=None):
        tier = {"normal": "allow", "medium": "step_up", "high": "block"}.get(tier, tier)
        model_name = model or DEFAULT_MODEL
        bundle = self.users.get(user_id)
        if model_name not in MODEL_NAMES:
            raise ValueError("Unknown model name")
        if not bundle or "scenarios" not in bundle or tier not in bundle["scenarios"][model_name]:
            raise ValueError("Curated scenarios are only available for trained synthetic demo profiles")
        return dict(bundle["scenarios"][model_name][tier])

    def assess(self, features, user_id="demo-irfan", model=None):
        model_name = model or DEFAULT_MODEL
        if model_name not in MODEL_NAMES:
            raise ValueError("Unknown model name")
        if user_id not in self.users:
            return {"model_score": None, "risk_score": None, "tier": "enrollment_required", "blocked": False,
                    "reasons": ["No genuine behavioural baseline is enrolled for this account; hold until verified enrolment"],
                    "model_version": VERSION, "model_name": model_name, "model_label": MODEL_NAMES[model_name],
                    "raw_thresholds": None, "assessments": {}, "data_source": self.data_source, "score_available": False}
        try:
            values = [float(features[name]) for name in FEATURES]
        except (TypeError, KeyError, ValueError) as error:
            raise ValueError("All seven behavioural features are required") from error
        if not np.isfinite(values).all():
            raise ValueError("Behavioural features must be finite")
        bundle = self.users[user_id]
        assessments = {name: _assessment(bundle, values, name) for name in MODEL_NAMES}
        result = dict(assessments[model_name])
        reasons = {"allow": ["Behaviour falls within the user's genuine-validation review boundary"],
                   "step_up": ["Behaviour exceeds the user's review threshold; additional verification required"],
                   "block": ["Behaviour exceeds the user's high-risk threshold; transaction must be blocked"]}
        result.update({"reasons": reasons[result["tier"]], "model_version": VERSION,
                       "assessments": assessments, "data_source": self.data_source})
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".runtime/ml"))
    parser.add_argument("--data", type=Path)
    args = parser.parse_args()
    print(json.dumps(train(args.output, args.data), indent=2))
