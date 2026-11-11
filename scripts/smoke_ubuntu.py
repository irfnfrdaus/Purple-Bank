"""Real Nginx proxy test on isolated loopback ports and a disposable database.

NGINX_PATH may select an existing binary; never installs/starts the host's Nginx
service or touches /etc. Run after setup; no Chromium or Android SDK needed.
"""
import os
from pathlib import Path
import pwd
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.ubuntu_deploy import render
from server.db import connect, digest
from server.ml import train


def unused_ports():
    with socket.socket() as first, socket.socket() as second:
        first.bind(("127.0.0.1", 0))
        second.bind(("127.0.0.1", 0))
        return first.getsockname()[1], second.getsockname()[1]


def main():
    executable = os.environ.get("NGINX_PATH") or shutil.which("nginx")
    if not executable:
        raise SystemExit("Nginx is optional. Install it separately or set NGINX_PATH for this proxy test.")
    executable = str(Path(executable).absolute())
    with tempfile.TemporaryDirectory(prefix="purple-proxy-") as folder:
        directory = Path(folder)
        data = directory / "data"
        train(data / "ml")
        backend_port, proxy_port = unused_ports()
        # Only used to render/test the candidate; no service is installed.
        user = next((p.pw_name for p in pwd.getpwall() if 1000 <= p.pw_uid < 65534), "www-data")
        _, candidate = render(ROOT, sys.executable, data, user, backend_port, proxy_port)
        server_file = directory / "server.conf"
        server_file.write_text(candidate)
        config = directory / "nginx.conf"
        config.write_text(f"""# Test process retains the invoking UID; never changes the host Nginx user.
user {pwd.getpwuid(os.getuid()).pw_name};
daemon off;
master_process off;
pid {directory}/nginx.pid;
error_log stderr notice;
events {{ worker_connections 64; }}
http {{
    access_log off;
    client_body_temp_path {directory}/body;
    proxy_temp_path {directory}/proxy;
    fastcgi_temp_path {directory}/fastcgi;
    uwsgi_temp_path {directory}/uwsgi;
    scgi_temp_path {directory}/scgi;
    include {server_file};
}}
""")
        subprocess.run([executable, "-p", str(directory), "-c", str(config), "-t"], check=True)
        processes = []
        with (directory / "run.log").open("w") as log:
            try:
                processes.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "server.app:app", "--port", str(backend_port),
                                                   "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"],
                                                  cwd=ROOT, env={**os.environ, "PURPLE_DATA_DIR": str(data)}, stdout=log, stderr=log))
                processes.append(subprocess.Popen([executable, "-p", str(directory), "-c", str(config)], stdout=log, stderr=log))
                base = f"http://127.0.0.1:{proxy_port}"
                with httpx.Client(base_url=base, trust_env=False) as client:
                    for _ in range(150):
                        if any(p.poll() is not None for p in processes):
                            raise RuntimeError((directory / "run.log").read_text())
                        try:
                            response = client.get("/api/health", timeout=1)
                            if response.status_code == 200:
                                break
                        except httpx.ConnectError:
                            pass
                        time.sleep(0.1)
                    else:
                        raise RuntimeError("Isolated reverse proxy did not become healthy")
                    assert response.json()["database"] == "ok"
                    for path in ("/", "/pin-demo", "/assets/pin-demo.js"):
                        assert client.get(path).status_code == 200
                    credentials = {"email": "irfan@purple.demo", "password": "PurpleDemo!2026", "device_id": "purple-demo-browser"}
                    response = client.post("/api/auth/login", json=credentials, headers={
                        "Origin": base, "X-Forwarded-For": "198.51.100.7", "X-Forwarded-Proto": "https"})
                    assert response.status_code == 200, response.text
                    assert "HttpOnly" in response.headers["set-cookie"]
                    assert "; Secure" not in response.headers["set-cookie"]  # Our proxy really serves private HTTP.
                    assert client.get("/api/overview").status_code == 200
                    assert client.post("/api/auth/login", json=credentials, headers={"Origin": "https://wrong-origin.invalid"}).status_code == 403
                    with connect(data / "purple.sqlite3") as db:
                        clients = {r[0] for r in db.execute("SELECT client_hash FROM login_attempts")}
                    assert clients == {digest("127.0.0.1")}, clients
                    print("PASS: real Nginx syntax/proxy; health; web/PIN/assets; same-origin login with port; session cookie; overview; cross-origin rejection; overwritten spoofed proxy headers")
            finally:
                for process in reversed(processes):
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


if __name__ == "__main__":
    main()
