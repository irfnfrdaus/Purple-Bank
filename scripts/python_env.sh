#!/usr/bin/env bash
# Source from setup.sh/start.sh after changing into the checkout root.
if [[ -n "${PURPLE_PYTHON:-}" ]]; then
  PYTHON="$PURPLE_PYTHON"
  if [[ ! -x "$PYTHON" ]]; then
    echo "PURPLE_PYTHON must point to an existing executable Python interpreter." >&2
    exit 1
  fi
elif [[ -x "$PWD/.venv/bin/python" ]]; then
  PYTHON="$PWD/.venv/bin/python"
elif [[ "$PWD" == /workspace/* && -x /workspace/.purple-venv/bin/python ]]; then
  PYTHON=/workspace/.purple-venv/bin/python
else
  PYTHON="$PWD/.venv/bin/python"
fi
