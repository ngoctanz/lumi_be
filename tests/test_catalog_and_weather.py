import time

import httpx
import pytest
from mongo_fixtures import find_model, insert_model, insert_models

from lumi_be.models import Asset, ProviderCache
from lumi_be.providers.weather import fetch_weather, refresh
from lumi_be.services import validate_cdn, weather_key


def test_private_assets_are_scoped_to_owner_and_hardware(app, client, users, hardware, claim):
    state = claim(hardware[0], users[0])
    claim(hardware[1], users[1])
    db = app.state.db
    own = Asset(
        owner_id=users[0][0],
        model="WazeHUD-CYD",
        name="Private A",
        url="https://cdn.example.test/a.raw",
        sha256="a" * 64,
        size=153600,
    )
    other = Asset(
        owner_id=users[1][0],
        model="WazeHUD-CYD",
        name="Private B",
        url="https://cdn.example.test/b.raw",
        sha256="b" * 64,
        size=153600,
    )
    public = Asset(
        model="WazeHUD-CYD", name="Shared", url="https://cdn.example.test/c.raw", sha256="c" * 64, size=153600
    )
    insert_models(db, [own, other, public])
    own_id, other_id, public_id = (own.id, other.id, public.id)
    assert client.get(f"/api/v1/device/assets/{own_id}", headers=hardware[0][1]).status_code == 200
    assert client.get(f"/api/v1/device/assets/{own_id}", headers=hardware[1][1]).status_code == 404
    assert client.get(f"/api/v1/device/assets/{public_id}", headers=hardware[1][1]).status_code == 200
    body = {"expected_revision": state["revision"], "config": {"asset_id": other_id}}
    path = f"/api/v1/me/devices/{hardware[0][0]}/config"
    assert client.put(path, headers=users[0][1], json=body).status_code == 404
    body["config"]["asset_id"] = own_id
    assert client.put(path, headers=users[0][1], json=body).status_code == 200
    assert len(client.get("/api/v1/device/snapshot", headers=hardware[0][1]).content) <= 512


def test_cdn_requires_exact_allowlisted_https_origin(app):
    assert validate_cdn("https://cdn.example.test/a.raw", app.state.settings)
    for url in (
        "http://cdn.example.test/a",
        "https://cdn.example.test.evil/a",
        "https://user:secret@cdn.example.test/a",
        "https://127.0.0.1/a",
        "https://cdn.example.test:8443/a",
    ):
        with pytest.raises(ValueError):
            validate_cdn(url, app.state.settings)


def test_weather_normalization_and_no_upstream_work_in_device_request():

    def handle(request):
        assert request.url.host == "api.open-meteo.com"
        return httpx.Response(
            200,
            json={
                "current": {
                    "temperature_2m": 30.2,
                    "apparent_temperature": 33.1,
                    "relative_humidity_2m": 80,
                    "weather_code": 95,
                    "wind_speed_10m": 10.1,
                },
                "daily": {"temperature_2m_max": [32.4], "temperature_2m_min": [25.5]},
            },
        )

    result = fetch_weather(16.0544, 108.2022, httpx.MockTransport(handle))
    assert result.c == 4 and result.temp == 30


def test_weather_refresh_failure_keeps_last_good_data(app, client, users, hardware, claim, monkeypatch):
    claim(hardware[0], users[0])
    db = app.state.db
    insert_model(
        db,
        ProviderCache(
            key=weather_key(16.0544, 108.2022), payload={"temp": 21}, updated_at=int(time.time()) - 4000
        ),
    )

    def broken(*_):
        raise httpx.ConnectError("upstream unavailable")

    monkeypatch.setattr("lumi_be.providers.weather.fetch_weather", broken)
    assert refresh(app.state.db) == {"updated": 0, "failed": 1}
    db = app.state.db
    assert find_model(db, "ProviderCache", weather_key(16.0544, 108.2022)).payload == {"temp": 21}
    assert find_model(db, "Device", hardware[0][0]).owner_id == users[0][0]
