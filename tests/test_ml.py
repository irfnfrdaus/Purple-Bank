"""Tests for genuine-only fitting, calibration, split integrity and score semantics."""
import csv
from pathlib import Path

import joblib
import numpy as np
import pytest

from server.ml import (CSV_FIELDS, DEFAULT_MODEL, FEATURES, MODEL_NAMES, RiskEngine,
                       equal_error_rate, raw_scores, read_dataset, synthetic_data, train,
                       validate_dataset)


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    output = tmp_path_factory.mktemp("oneclass-ml")
    metrics = train(output)
    return output, metrics, RiskEngine(output)


def write_csv(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_genuine_only_fit_and_validation_calibration(trained):
    output, metrics, _ = trained
    artifact = joblib.load(output / "model.joblib")
    rows = read_dataset(output / "training_data.csv")
    assert set(metrics["users"]) == {"demo-irfan", "demo-sarah", "demo-bookshop"}
    for uid, user in artifact["users"].items():
        training = np.array([[row[name] for name in FEATURES] for row in rows
                             if row["user_id"] == uid and row["split"] == "train"])
        validation = np.array([[row[name] for name in FEATURES] for row in rows
                               if row["user_id"] == uid and row["split"] == "validation"])
        for model in user["models"].values():
            # Scaler state independently verifies held-out/impostor rows did not fit it.
            assert model["pipeline"][0].n_samples_seen_ == 300
            np.testing.assert_allclose(model["pipeline"][0].mean_, training.mean(axis=0))
            expected_scores = raw_scores(model["pipeline"], validation)
            assert model["thresholds"]["review"] == pytest.approx(np.quantile(expected_scores, .95))
            assert model["thresholds"]["block"] == pytest.approx(np.quantile(expected_scores, .99))
    assert metrics["train_rows"] == 900
    assert metrics["validation_rows"] == 360
    assert metrics["test_rows"] == 1080
    assert metrics["default_model"] == DEFAULT_MODEL
    assert metrics["data_source"] == "synthetic_demo"


def test_heldout_metrics_and_error_definitions(trained):
    _, metrics, _ = trained
    for user in metrics["users"].values():
        assert user["counts"]["test_genuine"] == 120
        assert user["counts"]["test_impostor"] == 240
        for evaluation in user["models"].values():
            matrix = evaluation["confusion_matrix"]
            tn, fp = matrix[0]
            fn, tp = matrix[1]
            assert tn + fp == 120 and fn + tp == 240
            assert evaluation["far"] == pytest.approx(fn / 240)
            assert evaluation["frr"] == pytest.approx(fp / 120)
            assert evaluation["evaluation_threshold"] == "review"
            assert 0 <= evaluation["eer"] <= 1
            assert .5 < evaluation["roc_auc"] <= 1
    assert equal_error_rate([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 0


@pytest.mark.parametrize("model", MODEL_NAMES)
@pytest.mark.parametrize("tier", ["allow", "step_up", "block"])
def test_model_specific_curated_scenarios_are_actual_decisions(trained, model, tier):
    _, _, engine = trained
    values = engine.scenario_features("demo-irfan", tier, model=model)
    result = engine.assess(values, user_id="demo-irfan", model=model)
    assert result["tier"] == tier
    assert result["blocked"] is (tier == "block")
    assert result["score_available"] is True
    assert set(result["assessments"]) == set(MODEL_NAMES)
    assert 0 <= result["risk_score"] <= 1
    assert result["model_name"] == model
    threshold = result["raw_thresholds"]
    if tier == "allow":
        assert result["model_score"] < threshold["review"]
    elif tier == "step_up":
        assert threshold["review"] <= result["model_score"] < threshold["block"]
    else:
        assert result["model_score"] >= threshold["block"]


def test_unknown_user_has_no_fabricated_personal_model(trained):
    _, _, engine = trained
    assert engine.reference_profile("brand-new-account") is None
    result = engine.assess({}, user_id="brand-new-account")
    assert result["tier"] == "enrollment_required"
    assert result["model_score"] is None and result["risk_score"] is None
    assert result["score_available"] is False
    with pytest.raises(ValueError, match="synthetic demo profiles"):
        engine.scenario_features("brand-new-account")


def test_different_users_have_separate_baselines(trained):
    _, _, engine = trained
    first = engine.reference_profile("demo-irfan")
    second = engine.reference_profile("demo-sarah")
    assert first != second
    own = engine.assess(first, user_id="demo-irfan")
    other = engine.assess(first, user_id="demo-sarah")
    assert own["model_score"] != other["model_score"]
    assert own["risk_score"] < other["risk_score"]


def test_impostors_never_allowed_in_training_or_validation():
    for split in ["train", "validation"]:
        rows = synthetic_data()
        row = next(row for row in rows if row["split"] == split)
        row["label"] = "impostor"
        with pytest.raises(ValueError, match="evaluation-only"):
            validate_dataset(rows)


def test_session_overlap_and_invalid_numbers_rejected():
    rows = synthetic_data()
    validation = next(row for row in rows if row["split"] == "validation")
    training = next(row for row in rows if row["split"] == "train")
    validation["session_id"] = training["session_id"]
    with pytest.raises(ValueError, match="leakage"):
        validate_dataset(rows)
    rows = synthetic_data()
    rows[0]["dwell_ms"] = float("nan")
    with pytest.raises(ValueError, match="Non-finite"):
        validate_dataset(rows)
    rows = synthetic_data()
    rows[0]["touch_pressure"] = 2
    with pytest.raises(ValueError, match="bounds"):
        validate_dataset(rows)


def test_csv_import_schema_and_minimum_enrolment(tmp_path):
    invalid = tmp_path / "bad.csv"
    invalid.write_text("user_id,label\none,genuine\n")
    with pytest.raises(ValueError, match="Missing CSV columns"):
        read_dataset(invalid)
    with pytest.raises(ValueError, match="at least"):
        validate_dataset(synthetic_data()[:10])


def test_heldout_data_does_not_change_fitted_models_or_thresholds(trained, tmp_path):
    output, original, _ = trained
    rows = read_dataset(output / "training_data.csv")
    for row in rows:
        if row["split"] == "test":
            row["dwell_ms"] *= 4
    changed_output = tmp_path / "changed"
    changed = train(changed_output, write_csv(tmp_path / "changed.csv", rows))
    original_artifact = joblib.load(output / "model.joblib")
    changed_artifact = joblib.load(changed_output / "model.joblib")
    for uid in original["users"]:
        for model in MODEL_NAMES:
            assert original["users"][uid]["models"][model]["thresholds"] == changed["users"][uid]["models"][model]["thresholds"]
            np.testing.assert_array_equal(original_artifact["users"][uid]["models"][model]["pipeline"][0].mean_,
                                          changed_artifact["users"][uid]["models"][model]["pipeline"][0].mean_)
    assert changed["data_source"] == "user_supplied_csv"
    assert changed["users"]["demo-irfan"]["models"][DEFAULT_MODEL]["roc_auc"] != original["users"]["demo-irfan"]["models"][DEFAULT_MODEL]["roc_auc"]


def test_missing_and_nonfinite_inference_features_rejected(trained):
    _, _, engine = trained
    with pytest.raises(ValueError, match="required"):
        engine.assess({})
    features = engine.reference_profile()
    features["dwell_ms"] = float("inf")
    with pytest.raises(ValueError, match="finite"):
        engine.assess(features)
