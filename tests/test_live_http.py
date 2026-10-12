import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from lumi_be.models import Device
from lumi_be.security import digest, token

ROOT = Path(__file__).resolve().parents[1]


def test_live_http_account_claim_configuration_and_isolation(app):
    environment = {
        **os.environ,
        "LUMI_MONGODB_URI": app.state.settings.mongodb_uri.get_secret_value(),
        "LUMI_MONGODB_DATABASE": app.state.settings.mongodb_database,
    }
    secret = token("dev")
    device = Device(serial="LIVE-SIMULATOR", token_hash=digest(secret), created_at=int(time.time()))
    app.state.db.devices.insert_one(device.mongo())
    device_id = device.id
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "lumi_be.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=3) as client:
            for _ in range(100):
                try:
                    if client.get("/health/ready").status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(0.1)
            else:
                raise AssertionError("Live backend did not become ready")
            accounts = []
            for email in ("live-a@example.test", "live-b@example.test"):
                result = client.post(
                    "/api/v1/auth/register", json={"email": email, "password": "live-password-42"}
                )
                assert result.status_code == 201
                accounts.append({"Authorization": f"Bearer {result.json()['access_token']}"})
            auth = {"Authorization": f"Bearer {secret}"}
            code = client.post("/api/v1/device/pairing", headers=auth).json()["code"]
            claim = client.post(
                "/api/v1/me/devices/claim", headers=accounts[0], json={"device_id": device_id, "code": code}
            )
            assert claim.status_code == 200
            path = f"/api/v1/me/devices/{device_id}/config"
            body = {"expected_revision": claim.json()["revision"], "config": {"brightness": 40}}
            assert client.put(path, headers=accounts[1], json=body).status_code == 404
            assert client.put(path, headers=accounts[0], json=body).status_code == 200
            snapshot = client.get("/api/v1/device/snapshot", headers=auth)
            assert snapshot.json()["cfg"]["brightness"] == 40 and len(snapshot.content) <= 512
    finally:
        process.terminate()
        process.wait(timeout=10)
