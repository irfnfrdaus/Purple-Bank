"""Compile plain Java capture tests and validate its wire payloads with Python ML."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server.pin_rhythm import PinTiming, pin_engine


def main():
    jdk = os.environ.get("JAVA_HOME")
    cloud_jdk = Path("/workspace/tools/jdk-current.txt")
    if not jdk and cloud_jdk.exists():
        jdk = cloud_jdk.read_text().strip()
    javac = str(Path(jdk) / "bin/javac") if jdk else shutil.which("javac")
    java = str(Path(jdk) / "bin/java") if jdk else shutil.which("java")
    if not javac or not java:
        raise SystemExit("Install a complete JDK17+ or set JAVA_HOME; this is optional for running the web backend")
    source = ROOT / "android/app/src/main/java/my/edu/purplebank"
    with tempfile.TemporaryDirectory(prefix="purple-java-pin-") as output:
        subprocess.run([javac, "--release", "17", "-d", output, str(source / "PinRhythmCapture.java"),
                        str(source / "BackendUrl.java"), str(ROOT / "tests/android/PinRhythmCaptureTest.java")], check=True)
        result = subprocess.run([java, "-cp", output, "my.edu.purplebank.PinRhythmCaptureTest"], check=True, text=True, capture_output=True)
        vectors = [json.loads(line.split("\t", 1)[1]) for line in result.stdout.splitlines() if line.startswith("WIRE\t")]
        assert len(vectors) == 8
        for vector in vectors:
            prediction = pin_engine().evaluate(PinTiming(**vector))
            gap = prediction["features_measured"]["mean_flight_ms"]
            assert prediction["tier"] == ("allow" if gap == 200 else "block"), prediction
            assert not prediction["identity_verified"] and prediction["account_action"] == "none"
            assert set(vector) == {"strokes", "source", "model"}
        print(result.stdout.splitlines()[-1])
        print("8 Android wire payloads passed backend validation and model evaluation")


if __name__ == "__main__":
    main()
