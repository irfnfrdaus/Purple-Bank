"""PIN rhythm is a synthetic-reference exercise, never bank authentication."""
import copy
import csv
from pathlib import Path

import pytest

from server.db import connect
from server.pin_rhythm import PinTiming, pin_engine, export_reference, reference_sessions


def timing(gap=200, hold=110, source="pointer", model="isolation_forest"):
    return {
        "strokes": [{"down_ms": i * (hold + gap), "up_ms": i * (hold + gap) + hold} for i in range(6)],
        "source": source, "model": model,
    }


@pytest.mark.parametrize("model", ["isolation_forest", "one_class_svm"])
def test_shared_reference_distinguishes_slow_controlled_attempt_without_identity(model):
    normal = pin_engine().evaluate(PinTiming(**timing(model=model)))
    slow = pin_engine().evaluate(PinTiming(**timing(gap=1000, model=model)))
    assert normal["tier"] == "allow"
    assert slow["tier"] == "block"
    assert slow["features_measured"] == {"mean_dwell_ms": 110, "mean_flight_ms": 1000}
    assert slow["personal_baseline"] is slow["identity_verified"] is False
    assert slow["account_action"] == "none"
    # Input method is explanatory metadata, never a proxy for an impostor label.
    keyboard = pin_engine().evaluate(PinTiming(**timing(gap=1000, model=model, source="keyboard")))
    assert keyboard["tier"] == slow["tier"]
    assert keyboard["model_score"] == slow["model_score"]


@pytest.mark.parametrize("damage", [
    lambda p: p.update(strokes=p["strokes"][:5]),
    lambda p: p["strokes"].append(p["strokes"][-1]),
    lambda p: p["strokes"][0].update(down_ms=1),
    lambda p: p["strokes"][1].update(down_ms=90),
    lambda p: p["strokes"][0].update(up_ms=10),
    lambda p: p["strokes"][0].update(up_ms=4000),
    lambda p: p["strokes"][1].update(down_ms=6000, up_ms=6110),
    lambda p: p["strokes"][0].update(down_ms=float("nan")),
    lambda p: p["strokes"][0].update(up_ms=float("inf")),
    lambda p: p["strokes"][0].update(down_ms=False),
    lambda p: p["strokes"][0].update(up_ms="110"),
    lambda p: p["strokes"][0].update(digit="2"),
    lambda p: p.update(pin="246810"),
    lambda p: p.update(user_id="owner"),
    lambda p: p.update(source="impostor"),
    lambda p: p.update(model="unknown"),
])
def test_incomplete_forged_labels_or_invalid_timings_are_not_scored(damage):
    payload = timing()
    damage(payload)
    with pytest.raises(ValueError):
        PinTiming(**payload)


def test_public_exercise_does_not_authenticate_or_modify_bank_state(bank):
    client, app = bank
    tables = ("users", "sessions", "events", "transfers", "step_up_challenges", "devices")

    def snapshot():
        with connect(app.state.db_path) as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")] for table in tables}

    before = snapshot()
    assert client.get("/pin-demo").status_code == 200
    metadata = client.get("/api/demo/pin-rhythm").json()
    assert metadata["data_source"] == "synthetic_shared_reference"
    for model in ("isolation_forest", "one_class_svm"):
        for gap in (200, 1000):
            response = client.post("/api/demo/pin-rhythm", json=timing(gap=gap, model=model))
            assert response.status_code == 200
            assert "purple_session" not in response.cookies
    assert client.get("/api/me").status_code == 401
    assert snapshot() == before
    invalid = timing()
    invalid["pin"] = "246810"
    assert client.post("/api/demo/pin-rhythm", json=invalid).status_code == 422


def test_demo_model_is_repeatable_and_separate_from_seven_feature_engine(bank):
    client, app = bank
    bank_metadata = copy.deepcopy(app.state.risk.metrics)
    first = client.post("/api/demo/pin-rhythm", json=timing()).json()
    assert first == client.post("/api/demo/pin-rhythm", json=timing()).json()
    assert app.state.risk.metrics == bank_metadata
    assert first["version"] != bank_metadata["version"]
    assert set(first["features_measured"]) == {"mean_dwell_ms", "mean_flight_ms"}


def test_published_reference_matches_generator_and_export_never_overwrites(tmp_path):
    target = tmp_path / "pin.csv"
    export_reference(target)
    published = Path(__file__).resolve().parents[1] / "data/pin-rhythm-synthetic.csv"
    assert target.read_bytes() == published.read_bytes()
    with target.open() as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == 1000
    assert [r["split"] for r in rows] == ["train"] * 700 + ["validation"] * 300
    for row, values in zip(rows, reference_sessions()):
        assert [float(row["mean_dwell_ms"]), float(row["mean_flight_ms"])] == list(values)
        assert row["data_source"] == "synthetic_shared_reference"
    with pytest.raises(FileExistsError):
        export_reference(target)
    assert target.read_bytes() == published.read_bytes()
