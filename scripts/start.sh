#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/python_env.sh
if [[ ! -x "$PYTHON" ]]; then
  echo "Python environment is missing. Run bash scripts/setup.sh first." >&2
  exit 1
fi
exec "$PYTHON" -m uvicorn server.app:app --host "${PURPLE_HOST:-127.0.0.1}" --port "${PURPLE_PORT:-8000}"
