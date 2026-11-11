import shutil

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.ml import train


@pytest.fixture(scope="session")
def model_dir(tmp_path_factory):
    path = tmp_path_factory.mktemp("trained-model")
    train(path)
    return path


@pytest.fixture
def bank(tmp_path, model_dir):
    shutil.copytree(model_dir, tmp_path / "ml")
    app = create_app(tmp_path)
    with TestClient(app) as client:
        yield client, app


def login(client, admin=False, device="purple-demo-browser"):
    return client.post("/api/auth/login", json={
        "email": "admin@purple.demo" if admin else "irfan@purple.demo",
        "password": "PurpleAdmin!2026" if admin else "PurpleDemo!2026",
        "device_id": device,
    })
