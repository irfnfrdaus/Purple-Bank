"""Prepare host-specific private Ubuntu deployment files and verify real startup.

Rendering never writes /etc or changes services. The installer is deliberately
separate. No domain, login secret, database reset or firewall rule is invented.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import socket
import subprocess
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


def safe_path(value):
    path = Path(value).absolute()
    # systemd and nginx have different quoting/specifier rules. Keep deployment
    # paths simple instead of allowing injected directives or shell expansion.
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(path)):
        raise ValueError("Use absolute deployment paths without spaces or special characters")
    return path


def port_number(value):
    if isinstance(value, bool) or not str(value).isdigit() or not 1024 <= int(value) <= 65535:
        raise ValueError("Ports must be integers from 1024 to 65535")
    return int(value)


def require_free(port):
    with socket.socket() as listener:
        # Match Uvicorn's socket reuse: TIME_WAIT after our startup probe is
        # reusable, while an actual listener still makes bind fail.
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", port_number(port)))
        except OSError as error:
            raise RuntimeError(f"Loopback port {port} is unavailable; choose another port. No process was stopped.") from error


def render(checkout, python, data_dir, user, port=8000, proxy_port=8089):
    checkout, python, data_dir = map(safe_path, (checkout, python, data_dir))
    port, proxy_port = port_number(port), port_number(proxy_port)
    if port == proxy_port:
        raise ValueError("Backend and Nginx ports must differ")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
        raise ValueError("Invalid service user")
    if pwd.getpwnam(user).pw_uid == 0 or user == "nobody":
        raise ValueError("Run the service as a non-root Ubuntu account, not nobody")
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("Run bash scripts/setup.sh first; selected Python is not executable")
    if not (checkout / "server/app.py").is_file():
        raise ValueError("Select the real Purple Bank checkout")
    service = f"""# Generated for Purple Bank: {checkout}
[Unit]
Description=Purple Bank FYP private web demo
After=network.target

[Service]
Type=simple
User={user}
WorkingDirectory={checkout}
Environment=PURPLE_DATA_DIR={data_dir}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
UMask=0077
ExecStart={python} -m uvicorn server.app:app --host 127.0.0.1 --port {port} --proxy-headers --forwarded-allow-ips 127.0.0.1
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={data_dir}

[Install]
WantedBy=multi-user.target
"""
    nginx = f"""# Private candidate only. Review before enabling; preserves other sites.
server {{
    listen 127.0.0.1:{proxy_port};
    server_name localhost;
    client_max_body_size 64k;
    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
    }}
}}
"""
    return service, nginx


def check_running(port):
    """Read health/assets and exercise isolated PIN inference, never the ledger."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f"http://127.0.0.1:{port_number(port)}"

    def request(path, body=None):
        payload = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(base + path, data=payload, headers={"Content-Type": "application/json"} if payload else {})
        with opener.open(req, timeout=5) as response:
            content = response.read(100001)
            if len(content) > 100000:
                raise RuntimeError("Unexpected response size")
            return content

    health = json.loads(request("/api/health"))
    if health.get("status") != "ok" or health.get("database") != "ok" or health.get("model") != "purple-oneclass-v2":
        raise RuntimeError("Backend health/model validation failed")
    if b"Purple Bank" not in request("/") or b"pin-demo.js" not in request("/pin-demo"):
        raise RuntimeError("Web/PIN pages are unavailable")
    for model in ("isolation_forest", "one_class_svm"):
        for gap, tier in ((200, "allow"), (1000, "block")):
            payload = {"source": "keyboard", "model": model, "strokes": [
                {"down_ms": i * (110 + gap), "up_ms": i * (110 + gap) + 110} for i in range(6)]}
            result = json.loads(request("/api/demo/pin-rhythm", payload))
            if result.get("tier") != tier or result.get("account_action") != "none":
                raise RuntimeError("PIN inference check failed")
    return health


def probe(checkout, python, data_dir, port):
    """Start/stop only this child process; reject pre-existing listeners."""
    require_free(port)
    with tempfile.TemporaryDirectory(prefix="purple-startup-") as temporary:
        log_path = Path(temporary) / "startup.log"
        with log_path.open("w") as log:
            process = subprocess.Popen([str(python), "-m", "uvicorn", "server.app:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
                                       cwd=checkout, env={**os.environ, "PURPLE_DATA_DIR": str(data_dir)}, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Backend startup failed:\n" + log_path.read_text()[-4000:])
                if "Uvicorn running on" in log_path.read_text():
                    result = check_running(port)
                    if process.poll() is not None:
                        raise RuntimeError("Startup process exited during verification")
                    return result
                time.sleep(0.1)
            raise RuntimeError("Backend startup timed out; no existing service was stopped")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=ROOT / ".venv/bin/python")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("PURPLE_DATA_DIR", ROOT / ".runtime")))
    parser.add_argument("--user", default=pwd.getpwuid(os.getuid()).pw_name)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--proxy-port", type=int, default=8089)
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/deploy")
    parser.add_argument("--probe", action="store_true", help="Verify temporary startup before preparing files")
    parser.add_argument("--check-running", action="store_true", help="Check the already installed service; never starts/stops it")
    args = parser.parse_args()
    python, data_dir = safe_path(args.python), safe_path(args.data_dir.resolve())
    if args.check_running:
        print(json.dumps(check_running(args.port)))
        return
    service, nginx = render(ROOT, python, data_dir, args.user, args.port, args.proxy_port)
    if args.probe:
        print("Startup, database, web and PIN inference:", json.dumps(probe(ROOT, python, data_dir, args.port)))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "purplebank.service").write_text(service)
    (args.output / "purplebank.nginx.conf").write_text(nginx)
    print(f"Prepared {args.output}/purplebank.service and purplebank.nginx.conf")


if __name__ == "__main__":
    main()
