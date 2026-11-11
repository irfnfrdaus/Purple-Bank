import pwd
import socket
import sys
from pathlib import Path

import pytest

from scripts.ubuntu_deploy import ROOT, port_number, probe, render, require_free


def service_user():
    return next((p.pw_name for p in pwd.getpwall() if 1000 <= p.pw_uid < 65534), "www-data")


def test_generated_deployment_uses_exact_host_paths_and_private_proxy(tmp_path):
    # Include a non-default data location to prove deployments preserve it.
    data = tmp_path / "existing-research"
    data.mkdir()
    sentinel = data / "keep.txt"
    sentinel.write_text("existing data")
    unit, nginx = render(ROOT, sys.executable, data, service_user(), 18022, 18023)
    assert f"Environment=PURPLE_DATA_DIR={data}" in unit
    assert f"WorkingDirectory={ROOT}" in unit
    assert "ReadWritePaths=" + str(data) in unit and "ProtectSystem=strict" in unit
    assert "--host 127.0.0.1 --port 18022" in unit
    assert "listen 127.0.0.1:18023;" in nginx
    # Preserve the request's port for the application's Origin/Host boundary.
    assert "proxy_set_header Host $http_host;" in nginx
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in nginx
    assert "0.0.0.0" not in nginx and "REPLACE" not in unit
    assert sentinel.read_text() == "existing data"


@pytest.mark.parametrize("bad", [80, 0, 65536, "8000; reboot", "8000\n", True])
def test_invalid_privileged_or_injected_ports_rejected(bad):
    with pytest.raises(ValueError):
        port_number(bad)


def test_root_or_injected_paths_and_colliding_ports_rejected(tmp_path):
    with pytest.raises(ValueError):
        render(ROOT, sys.executable, tmp_path, "root")
    with pytest.raises(ValueError):
        render(ROOT, sys.executable, tmp_path, "nobody")
    for suffix in ("bad path", "bad%path", "bad\npath", "bad$path"):
        with pytest.raises(ValueError):
            render(ROOT, sys.executable, tmp_path / suffix, service_user())
    with pytest.raises(ValueError):
        render(ROOT, sys.executable, tmp_path, service_user(), 8000, 8000)


def test_startup_probe_rejects_other_listener_and_leaves_it_alive():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.listen()
        with pytest.raises(RuntimeError):
            require_free(port)
        with pytest.raises(RuntimeError):
            probe(ROOT, sys.executable, ROOT / ".runtime", port)
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass


def test_real_startup_health_and_pin_inference_preserve_models(tmp_path, model_dir):
    import shutil
    shutil.copytree(model_dir, tmp_path / "ml")
    artifact = tmp_path / "ml/model.joblib"
    before = artifact.read_bytes()
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        port = candidate.getsockname()[1]
    health = probe(ROOT, sys.executable, tmp_path, port)
    assert health["status"] == health["database"] == "ok"
    assert health["model"] == "purple-oneclass-v2"
    assert artifact.read_bytes() == before
    require_free(port)  # The test child was cleaned up; no listener was killed.
