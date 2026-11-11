"""Isolated PIN timing demonstration with a shared synthetic reference.

This is not account authentication or a personal biometric model. No PIN digits,
device identifiers or participant data are accepted or persisted here.
"""
from functools import lru_cache
import csv
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import OneClassSVM


class Stroke(BaseModel):
    model_config = ConfigDict(extra="forbid")
    down_ms: float = Field(ge=0, le=45000, strict=True, allow_inf_nan=False)
    up_ms: float = Field(ge=0, le=45000, strict=True, allow_inf_nan=False)


class PinTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strokes: list[Stroke] = Field(min_length=6, max_length=6)
    source: Literal["pointer", "keyboard"]
    model: Literal["isolation_forest", "one_class_svm"] = "isolation_forest"

    @model_validator(mode="after")
    def valid_timing(self):
        if self.strokes[0].down_ms != 0:
            raise ValueError("Timing must start at zero; do not send wall-clock timestamps")
        for index, stroke in enumerate(self.strokes):
            if not 15 <= stroke.up_ms - stroke.down_ms <= 3000:
                raise ValueError("Each press must last 15–3000 ms; enter the whole PIN again")
            if index and not 20 <= stroke.down_ms - self.strokes[index - 1].up_ms <= 5000:
                raise ValueError("Release each digit before the next; gaps must be 20–5000 ms")
        return self

    def features(self):
        dwell = [s.up_ms - s.down_ms for s in self.strokes]
        flight = [b.down_ms - a.up_ms for a, b in zip(self.strokes, self.strokes[1:])]
        return np.array([np.mean(dwell), np.mean(flight)], dtype=float)


def reference_sessions():
    """Repeatable shared synthetic session means; never real owner data."""
    rng = np.random.default_rng(246810)
    return np.column_stack([
        rng.lognormal(np.log(110), 0.23, 1000),
        rng.lognormal(np.log(200), 0.25, 1000),
    ])


def export_reference(path: Path):
    """Exclusive create protects existing experiment/data files."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", newline="", encoding="utf-8") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["session_id", "split", "mean_dwell_ms", "mean_flight_ms", "data_source"])
        for index, (dwell, flight) in enumerate(reference_sessions()):
            writer.writerow([f"synthetic-pin-{index:04d}", "train" if index < 700 else "validation",
                             format(dwell, ".17g"), format(flight, ".17g"), "synthetic_shared_reference"])


class PinRhythm:
    version = "purple-pin-synthetic-v1"

    def __init__(self):
        # Independent generated sessions. No real user data and no impostor
        # labels are used to fit models or choose validation thresholds.
        sessions = reference_sessions()
        train, validation = sessions[:700], sessions[700:]
        self.models = {
            "isolation_forest": make_pipeline(StandardScaler(), IsolationForest(n_estimators=100, random_state=42, n_jobs=1)),
            "one_class_svm": make_pipeline(StandardScaler(), OneClassSVM(nu=0.05, gamma="scale")),
        }
        self.thresholds = {}
        for name, model in self.models.items():
            model.fit(train)
            scores = -model.score_samples(validation)
            self.thresholds[name] = dict(zip(("review", "block"), map(float, np.quantile(scores, [0.95, 0.99]))))

    def metadata(self):
        return {
            "version": self.version, "data_source": "synthetic_shared_reference",
            "personal_baseline": False, "identity_verified": False,
            "features": ["mean_dwell_ms", "mean_flight_ms"],
            "train_sessions": 700, "validation_sessions": 300,
            "reference_median_ms": {"mean_dwell_ms": 110, "mean_flight_ms": 200},
            "thresholds": self.thresholds,
            "scope": "Isolated timing exercise; does not grant login, lock accounts or move money",
        }

    def evaluate(self, timing: PinTiming):
        values = timing.features()
        score = float(-self.models[timing.model].score_samples(values.reshape(1, -1))[0])
        thresholds = self.thresholds[timing.model]
        tier = "block" if score >= thresholds["block"] else "step_up" if score >= thresholds["review"] else "allow"
        return {
            **self.metadata(), "model": timing.model, "tier": tier,
            "score_available": True, "model_score": score,
            "features_measured": dict(zip(("mean_dwell_ms", "mean_flight_ms"), map(float, values))),
            "samples": {"presses": 6, "gaps": 5}, "input_source": timing.source,
            "account_action": "none",
            "interpretation": "Timing differs from reference timing" if tier != "allow" else "Timing is within reference timing",
            "limitation": "Slow owners can be flagged; other people with similar timing can pass. Client timings can be forged. This is not identity verification.",
        }


@lru_cache(maxsize=1)
def pin_engine():
    return PinRhythm()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Export the PIN exercise synthetic reference; never overwrites existing data")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    export_reference(arguments.output)
    print(f"Created 700 train + 300 validation synthetic sessions: {arguments.output}")
