import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from lumi_be.app import create_app
from lumi_be.config import Settings
from lumi_be.db import initialize
from lumi_be.models import Device
from lumi_be.security import digest, token


@pytest.fixture
def app():
    # Atlas limits database names to 38 bytes. Keep random test databases well below it.
    name = "lumi_t_" + uuid.uuid4().hex[:24]
    app = create_app(
        Settings(
            mongodb_uri=os.getenv("LUMI_TEST_MONGODB_URI", "mongodb://127.0.0.1:27018"),
            mongodb_database=name,
            cdn_hosts=["cdn.example.test"],
        )
    )
    initialize(app.state.db)
    # Keep a separate cleanup client because TestClient lifespan closes the app pool.
    from pymongo import MongoClient

    cleanup = MongoClient(app.state.settings.mongodb_uri.get_secret_value())
    yield app
    cleanup.drop_database(name)
    cleanup.close()
    app.state.mongo_client.close()


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def users(client):
    def register(email):
        response = client.post("/api/v1/auth/register", json={"email": email, "password": "correct-horse-42"})
        assert response.status_code == 201, response.text
        body = response.json()
        return body["user"]["id"], {"Authorization": f"Bearer {body['access_token']}"}

    return register("owner-a@example.test"), register("owner-b@example.test")


@pytest.fixture
def hardware(app):
    def provision(serial):
        secret = token("dev")
        device = Device(serial=serial, token_hash=digest(secret), created_at=int(time.time()))
        app.state.db.devices.insert_one(device.mongo())
        return device.id, {"Authorization": f"Bearer {secret}"}

    return provision("CYD-A"), provision("CYD-B")


@pytest.fixture
def claim(client):
    def bind(hardware, account):
        device_id, device_auth = hardware
        code = client.post("/api/v1/device/pairing", headers=device_auth)
        assert code.status_code == 201, code.text
        response = client.post(
            "/api/v1/me/devices/claim",
            headers=account[1],
            json={"device_id": device_id, "code": code.json()["code"]},
        )
        assert response.status_code == 200, response.text
        return response.json()

    return bind
