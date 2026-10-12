import time
from uuid import uuid4

from mongo_fixtures import find_model, insert_model

from lumi_be.models import ProviderCache
from lumi_be.services import weather_key


def test_snapshot_is_bounded_with_weather_and_offline_defaults(app, client, users, hardware, claim):
    claim(hardware[0], users[0])
    db = app.state.db
    device = find_model(db, "Device", hardware[0][0])
    device.config = {
        "asset_id": str(uuid4()),
        "brightness": 100,
        "show_eta": False,
        "show_alerts": False,
        "theme": "night",
        "wallpaper": "sunset",
    }
    device.revision = 2147483647
    insert_model(
        db,
        ProviderCache(
            key=weather_key(16.0544, 108.2022),
            payload={"temp": -90, "feels": -110, "hi": -90, "lo": -90, "hum": 100, "wind": 500, "c": 4},
            updated_at=int(time.time()),
        ),
    )
    response = client.get("/api/v1/device/snapshot", headers=hardware[0][1])
    assert response.status_code == 200, response.text
    assert len(response.content) <= 512
    assert response.json()["weather"]["temp"] == -90
    assert response.json()["offline"] == {"keep": True, "nav_ttl_ms": 3000}
    assert response.json()["cfg"]["wallpaper"] == "sunset"
    assert "owner_id" not in response.text and "password" not in response.text


def test_hello_identity_cannot_be_redirected(client, hardware):
    body = {"firmware": "2.0.0-alpha.31-ota-diag", "build": 31, "applied_revision": 0}
    response = client.post("/api/v1/device/hello", headers=hardware[0][1], json=body)
    assert response.status_code == 200
    assert response.json()["id"] == hardware[0][0]
    body["device_id"] = hardware[1][0]
    assert client.post("/api/v1/device/hello", headers=hardware[0][1], json=body).status_code == 422


def test_configuration_cas_and_cross_account_isolation(client, users, hardware, claim):
    state = claim(hardware[0], users[0])
    device_id = hardware[0][0]
    body = {"expected_revision": state["revision"], "config": {"brightness": 50, "theme": "night"}}
    path = f"/api/v1/me/devices/{device_id}/config"
    assert client.put(path, headers=users[1][1], json=body).status_code == 404
    result = client.put(path, headers=users[0][1], json=body)
    assert result.status_code == 200
    assert client.put(path, headers=users[0][1], json=body).status_code == 409
    assert client.get("/api/v1/device/snapshot", headers=hardware[0][1]).json()["cfg"]["brightness"] == 50
    assert client.get("/api/v1/device/snapshot", headers=hardware[1][1]).json()["cfg"]["brightness"] == 70


def test_layout_geometry_schema_and_conditional_request(client, users, hardware, claim):
    state = claim(hardware[0], users[0])
    layout = {
        "version": 1,
        "widgets": [
            {"id": "clock-main", "type": "clock", "x": 8, "y": 8, "width": 96, "height": 108, "visible": True}
        ],
    }
    path = f"/api/v1/me/devices/{hardware[0][0]}/layout"
    body = {"expected_revision": state["revision"], "layout": layout}
    assert client.put(path, headers=users[0][1], json=body).status_code == 200
    reply = client.get("/api/v1/device/layout", headers=hardware[0][1])
    assert len(reply.content) <= 4096
    assert reply.json()["layout"] == layout
    headers = {**hardware[0][1], "If-None-Match": reply.headers["ETag"]}
    assert client.get("/api/v1/device/layout", headers=headers).status_code == 304
    body["expected_revision"] += 1
    layout["widgets"][0]["x"] = 300
    assert client.put(path, headers=users[0][1], json=body).status_code == 422
    layout["version"] = 2
    assert client.put(path, headers=users[0][1], json=body).status_code == 422


def test_navigation_full_snapshot_byte_limit_and_staleness(app, client, users, hardware, claim):
    claim(hardware[0], users[0])
    path = f"/api/v1/me/devices/{hardware[0][0]}/navigation"
    assert client.post(path, headers=users[0][1], json={"nav": 1, "spd": 55, "avg": 1}).status_code == 202
    result = client.get("/api/v1/device/navigation", headers=hardware[0][1])
    assert result.json()["avg"] == 1
    assert result.json()["spd"] == 55
    assert "X-Frame-Age-Ms" in result.headers
    assert client.post(path, headers=users[0][1], json={"spd": 10}).status_code == 202
    assert "nav" not in client.get("/api/v1/device/navigation", headers=hardware[0][1]).json()
    db = app.state.db
    find_model(db, "Device", hardware[0][0]).navigation_at_ms = int(time.time() * 1000) - 3001
    assert client.get("/api/v1/device/navigation", headers=hardware[0][1]).status_code == 204
    assert client.get("/api/v1/device/navigation", headers=hardware[1][1]).status_code == 204
    assert client.post(path, headers=users[0][1], json={"st": "ế" * 40}).status_code == 422


def test_standard_errors_body_bounds_and_no_secret_echo(client, hardware):
    response = client.post("/api/v1/auth/login", json={"password": "secret-do-not-echo"})
    assert response.status_code == 422
    assert "secret-do-not-echo" not in response.text
    assert response.json()["error"]["code"] == "validation_error"
    assert response.headers["X-Request-ID"]
    response = client.post("/api/v1/device/hello", headers=hardware[0][1], content=b"x" * 20000)
    assert response.status_code == 413
    assert len(response.content) <= 512
    response = client.get("/api/v1/device/snapshot")
    assert response.status_code == 401
    assert response.json()["t"] == "err"
    response = client.get("/api/v1/device/missing")
    assert response.status_code == 404
    assert response.json()["t"] == "err"


def test_cached_weather_outage_does_not_remove_builtin_fallback(app, client, users, hardware, claim):
    claim(hardware[0], users[0])
    db = app.state.db
    insert_model(
        db,
        ProviderCache(
            key=weather_key(16.0544, 108.2022), payload={"temp": 22}, updated_at=int(time.time()) - 90000
        ),
    )
    data = client.get("/api/v1/device/snapshot", headers=hardware[0][1]).json()
    assert data["weather"] is None
    assert data["cfg"]["wallpaper"] == "sunset"
    assert data["offline"]["keep"] is True
