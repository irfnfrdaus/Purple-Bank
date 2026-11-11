# Purple Bank — Behavioural Biometrics Research Prototype

Purple Bank is a Final Year Project (FYP) web and native Android mobile banking prototype exploring per-user behavioural anomaly detection and PIN timing verification for simulated fund transfers.

## Key Capabilities

- **Risk-Based Transaction Pipeline (`server/`):** FastAPI backend with an atomic SQLite ledger that evaluates behavioural signals before any balance change (`allow`, `step_up`, or `block` with account locking and session revocation).
- **One-Class ML Engine (`server/ml.py`):** Per-user `Isolation Forest` and `One-Class SVM` pipelines (`StandardScaler` fit strictly on genuine training sessions; review/block quantiles calibrated at 95th/99th percentiles on separate genuine validation sessions; held-out genuine and cross-user impostor evaluation reporting EER, FAR, FRR, and ROC-AUC).
- **Measured PIN Rhythm Policy (`server/account_pin_rhythm.py`, `server/pin_rhythm.py`):** Captures monotonic key/touch dwell and flight timings (without storing PIN digits in timing telemetry) and supports a standalone interactive PIN practice exercise (`/pin-demo`).
- **Android Hardware-Backed Step-Up (`android/`, `server/biometric.py`):** Native Android application (`my.edu.purplebank`) with timed PIN keypad capture and Android Keystore `BIOMETRIC_STRONG` ECDSA/SHA-256 signature verification bound to user, device, session, and transfer challenges.
- **Responsive Web Interface (`web/`):** Customer dashboard, transfer flow, security scenario console, interactive research comparison lab, presentation view, and administrator review queue.

## Repository Structure

```text
├── android/     # Native Android Java application (Gradle project)
├── data/        # Reproducible synthetic reference CSVs & provenance notes
├── deploy/      # Reverse-proxy and systemd deployment templates
├── scripts/     # Setup, model preparation, Android build, and smoke scripts
├── server/      # FastAPI application, SQLite ledger, ML & biometric modules
├── tests/       # Python API/ML/security, Node timing, and Java/Robolectric tests
└── web/         # Static frontend assets (HTML, CSS, vanilla JS)
```

## Quick Start (Backend & Web)

1. Create a Python virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   # Linux/macOS: source .venv/bin/activate
   # Windows:     .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Prepare the default one-class synthetic reference models:

   ```bash
   python -m scripts.prepare_models
   ```

3. Run the web server locally:

   ```bash
   python -m uvicorn server.app:app --host 127.0.0.1 --port 8000
   ```

   Open `http://127.0.0.1:8000` in your browser (or `http://127.0.0.1:8000/pin-demo` for the standalone PIN rhythm exercise).

## Demo Accounts & Research Boundaries

- **Demo Customer:** `irfan@purple.demo` / `PurpleDemo!2026`
- **Security Analyst (Admin):** `admin@purple.demo` / `PurpleAdmin!2026`
- All funds, transfers, and balances are synthetic prototype simulations in MYR.
- Included baseline profiles (`demo-irfan`, `demo-sarah`, `demo-bookshop`) and PIN timing references (`data/`) use deterministic synthetic generation (`server/ml.py`, `server/pin_rhythm.py`, `server/account_pin_rhythm.py`).

## Verification

Run the automated test suites:

```bash
pytest -q
node tests/pin_rhythm_capture.cjs
```

For Android unit tests and builds (requires JDK 17 and Android SDK 35):

```bash
cd android
./gradlew testDebugUnitTest assembleDebug lintDebug
```
