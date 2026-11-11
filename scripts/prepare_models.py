"""Prepare the demo without silently replacing a researcher-supplied dataset/model."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import shutil

from server.ml import RiskEngine, VERSION, train

directory = Path(os.environ.get("PURPLE_DATA_DIR", ".runtime")) / "ml"
artifact = directory / "model.joblib"
if artifact.exists():
    metadata = json.loads((directory / "metrics.json").read_text())
    if metadata.get("version") == VERSION:
        RiskEngine(directory)
        print("Existing one-class model retained; explicit training is required to replace it")
    elif str(metadata.get("data_source", "")).startswith("synthetic"):
        backup = directory.with_name("ml-previous-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
        shutil.copytree(directory, backup)
        train(directory)
        print("Previous synthetic artifact backed up; one-class demo prepared")
    else:
        raise SystemExit("Existing research model is incompatible. Review its data and retrain explicitly; setup has not replaced it.")
else:
    train(directory)
    print("Per-user one-class synthetic demo prepared")
