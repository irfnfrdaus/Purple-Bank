#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/python_env.sh
if [[ ! -x "$PYTHON" ]]; then
  BASE_PYTHON=python3
  if command -v python3.12 >/dev/null 2>&1; then BASE_PYTHON=python3.12; fi
  "$BASE_PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 12), "Use Python 3.12 (Ubuntu 24.04: install python3.12-venv)"'
  "$BASE_PYTHON" -m venv "$PWD/.venv"
fi
"$PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 12), "This lockfile is tested on Python 3.12"'
"$PYTHON" -m pip install -r requirements.lock
"$PYTHON" -m scripts.prepare_models
"$PYTHON" -m pytest -q
