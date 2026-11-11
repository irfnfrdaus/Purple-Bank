#!/usr/bin/env bash
# First installation only, from a normal Ubuntu account with sudo.
set -euo pipefail
cd "$(dirname "$0")/.."
PURPLE_CHECKOUT="$PWD"
PURPLE_INSTALL_PORT=8000
PURPLE_PROXY_PORT=8089
while (($#)); do
  case "$1" in
    --port) PURPLE_INSTALL_PORT="${2:?Missing backend port}"; shift 2 ;;
    --proxy-port) PURPLE_PROXY_PORT="${2:?Missing private Nginx port}"; shift 2 ;;
    *) echo "Usage: bash scripts/install_ubuntu.sh [--port 8000] [--proxy-port 8089]" >&2; exit 2 ;;
  esac
done
if [[ "$(id -u)" == 0 ]]; then
  echo "Run as the normal Ubuntu login user with sudo access, not as root." >&2; exit 1
fi
source /etc/os-release
if [[ "${ID:-}" != ubuntu || "${VERSION_ID:-}" != 24.04 || "$(uname -m)" != x86_64 ]]; then
  echo "This installer targets Ubuntu 24.04 x86_64. Use the manual guide for another host." >&2; exit 1
fi
if [[ ! -d /run/systemd/system ]]; then
  echo "A real Ubuntu systemd host is required; this environment is not a persistent server." >&2; exit 1
fi
command -v python3.12 >/dev/null || { echo "Install python3.12 and python3.12-venv first (README.md)." >&2; exit 1; }
command -v sudo >/dev/null || { echo "sudo is required for installing this service." >&2; exit 1; }
# Reuse existing non-interactive privilege; mixed sudoers rules may still make -v prompt.
sudo -n true 2>/dev/null || sudo -v
if sudo systemctl cat purplebank.service >/dev/null 2>&1 || sudo test -e /etc/systemd/system/purplebank.service; then
  echo "purplebank.service already exists. No files changed. Use the update procedure in README.md." >&2; exit 1
fi
python3.12 - "$PURPLE_INSTALL_PORT" "$PURPLE_PROXY_PORT" <<'PY'
import sys
from scripts.ubuntu_deploy import port_number, require_free
ports = [port_number(p) for p in sys.argv[1:]]
if len(set(ports)) != 2:
    raise SystemExit("Backend and private Nginx ports must differ")
require_free(ports[0])
require_free(ports[1])
PY
# Existing database/model/data selections are retained by the normal setup.
bash scripts/setup.sh
source scripts/python_env.sh
"$PYTHON" scripts/ubuntu_deploy.py --python "$PYTHON" --user "$(id -un)" \
  --port "$PURPLE_INSTALL_PORT" --proxy-port "$PURPLE_PROXY_PORT" --probe
sudo systemd-analyze verify "$PURPLE_CHECKOUT/.runtime/deploy/purplebank.service"
PURPLE_UNIT_CREATED=0
rollback() {
  if [[ "$PURPLE_UNIT_CREATED" == 1 ]]; then
    # Only this newly created unit is removed. Runtime data is never deleted.
    sudo systemctl disable --now purplebank.service || true
    sudo rm -f /etc/systemd/system/purplebank.service
    sudo systemctl daemon-reload
  fi
}
trap rollback ERR
# Exclusive creation prevents overwriting a service installed during preflight.
sudo python3.12 - "$PURPLE_CHECKOUT/.runtime/deploy/purplebank.service" <<'PY'
import os
from pathlib import Path
import sys
source = Path(sys.argv[1]).read_bytes()
target = Path("/etc/systemd/system/purplebank.service")
descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
try:
    with os.fdopen(descriptor, "wb") as output:
        output.write(source)
except BaseException:
    target.unlink()
    raise
PY
PURPLE_UNIT_CREATED=1
sudo chmod 0644 /etc/systemd/system/purplebank.service
sudo systemctl daemon-reload
sudo systemctl enable --now purplebank.service
PURPLE_READY=0
for ((attempt=0;attempt<30;attempt++)); do
  if sudo systemctl is-active --quiet purplebank.service && "$PYTHON" scripts/ubuntu_deploy.py --port "$PURPLE_INSTALL_PORT" --check-running >/dev/null 2>&1; then
    PURPLE_READY=1; break
  fi
  if ! sudo systemctl is-active --quiet purplebank.service; then break; fi
  sleep 1
done
if [[ "$PURPLE_READY" != 1 ]]; then
  echo "Service did not pass health/inference validation. See sudo journalctl -u purplebank -n 60." >&2
  false
fi
trap - ERR
echo "Installed and enabled purplebank.service. Private web: http://127.0.0.1:$PURPLE_INSTALL_PORT"
echo "PIN demo: http://127.0.0.1:$PURPLE_INSTALL_PORT/pin-demo"
echo "Nginx candidate prepared at .runtime/deploy/purplebank.nginx.conf; no existing Nginx site was changed."
echo "For laptop access: ssh -N -L 18000:127.0.0.1:$PURPLE_INSTALL_PORT USER@SERVER"
