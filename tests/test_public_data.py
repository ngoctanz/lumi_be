import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
from fastapi import HTTPException

from lumi_be.db import initialize, ready, utc_date
from lumi_be.models import Asset
from lumi_be.providers.registry import cache_key, normalize
from lumi_be.services import limit


def test_account_and_device_control_are_removed(client):
    paths = [
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/me/devices/claim",
        "/api/v1/me/devices/enroll",
        "/api/v1/device/hello",
    ]
    for path in paths:
        assert client.post(path, json={}).status_code == 404
    assert client.get("/api/v1/widget-sources").status_code == 200


def test_public_catalogue_never_exposes_former_private_images(app, client):
    for owner in (None, "former-user-a", "former-user-b"):
        item = Asset(
            model="WazeHUD-CYD",
            name=str(owner),
            owner_id=owner,
            url="https://cdn.example.test/a.raw",
            sha256="a" * 64,
            size=153600,
        )
        app.state.db.assets.insert_one(item.mongo())
    response = client.get("/api/v1/assets").json()
    assert [item["name"] for item in response["items"]] == ["None"]
    assert client.post("/api/v1/assets", json={}).status_code == 405
    assert client.get("/api/v1/assets?model=other").status_code == 422


def test_weather_is_bounded_cached_and_has_no_account_config(app, client, monkeypatch):
    calls = []

    def fetch(source, params):
        calls.append(params)
        return {"temp": 30, "feels": 33, "hi": 32, "lo": 25, "hum": 80, "wind": 10, "c": 1}

    monkeypatch.setattr("lumi_be.routers.public_data.fetch", fetch)
    url = "/api/v1/widgets/weather?latitude=10.1234&longitude=106.1234"
    first = client.get(url)
    second = client.get(url)
    assert first.status_code == second.status_code == 200
    assert len(first.content) <= 512
    assert first.json()["state"] == "fresh"
    assert first.json()["data"]["temp"] == 30
    assert len(calls) == 1
    assert calls[0] == {"latitude": 10.12, "longitude": 106.12}
    assert app.state.db.devices.count_documents({}) == 0
    assert client.get("/api/v1/widgets/weather?latitude=nan").status_code == 422
    assert client.get("/api/v1/widgets/weather?latitude=91").status_code == 422


def test_upstream_failure_preserves_stale_data_and_delays_retry(app, client, monkeypatch):
    params = normalize("open-meteo-weather-v1", {"latitude": 10, "longitude": 106})
    key = cache_key("open-meteo-weather-v1", params)
    app.state.db.provider_cache.insert_one(
        {
            "_id": key,
            "source_id": "open-meteo-weather-v1",
            "payload": {"temp": 25},
            "updated_at": int(time.time()) - 4000,
            "expire_at": utc_date(time.time() + 86400),
        }
    )
    calls = []

    def fail(*_):
        calls.append(True)
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("lumi_be.routers.public_data.fetch", fail)
    url = "/api/v1/widgets/weather?latitude=10&longitude=106"
    response = client.get(url)
    assert response.json()["state"] == "stale"
    assert response.json()["data"]["temp"] == 25
    assert client.get(url).json()["data"]["temp"] == 25
    assert len(calls) == 1


def test_cache_miss_without_upstream_returns_pending(client, monkeypatch):
    def fail(*_):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("lumi_be.routers.public_data.fetch", fail)
    response = client.get("/api/v1/widgets/weather?latitude=1&longitude=1")
    assert response.status_code == 200
    assert response.json()["state"] == "pending"
    assert response.json()["data"] is None


def test_public_cache_schema_is_idempotent_and_preserves_legacy_records(app):
    app.state.db.users.insert_one({"_id": "legacy", "email": "keep@example.test"})
    initialize(app.state.db)
    assert ready(app.state.db)
    assert app.state.db.users.find_one({"_id": "legacy"})
    for name in ("rate_limits", "provider_cache"):
        assert any(
            item.get("expireAfterSeconds") == 0 for item in app.state.db[name].index_information().values()
        )


def test_limiter_is_atomic_under_concurrent_clients(app):
    request = SimpleNamespace(app=app)

    def compete(_):
        try:
            limit(request, "atomic-public-limit", 5, seconds=86400)
            return 200
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        codes = list(pool.map(compete, range(30)))
    assert codes.count(200) == 5 and codes.count(429) == 25


def test_simultaneous_weather_misses_fetch_upstream_once(client, monkeypatch):
    calls = []

    def fetch(*_):
        calls.append(True)
        time.sleep(0.05)
        return {"temp": 25}

    monkeypatch.setattr("lumi_be.routers.public_data.fetch", fetch)
    with ThreadPoolExecutor(max_workers=8) as pool:
        replies = list(
            pool.map(lambda _: client.get("/api/v1/widgets/weather?latitude=11&longitude=107"), range(8))
        )
    assert all(reply.status_code == 200 for reply in replies)
    assert len(calls) == 1
