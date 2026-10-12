import time

from mongo_fixtures import find_model, insert_model

from lumi_be.models import ProviderCache
from lumi_be.providers.registry import cache_key
from lumi_be.providers.weather import Weather
from lumi_be.providers.widgets import refresh
from lumi_be.services import weather_key

SOURCE = "open-meteo-weather-v1"
PARAMS = {"latitude": 16.0544, "longitude": 108.2022}


def set_weather(client, hardware, account, revision, **extra):
    return client.put(
        f"/api/v1/me/devices/{hardware[0]}/widgets/weather",
        headers=account[1],
        json={"expected_revision": revision, "source_id": SOURCE, "params": PARAMS, **extra},
    )


def test_catalog_binding_scope_validation_revision_and_unbind(app, client, users, hardware, claim):
    a = claim(hardware[0], users[0])
    claim(hardware[1], users[1])
    assert client.get("/api/v1/widget-sources").status_code == 401
    catalog = client.get("/api/v1/widget-sources", headers=users[0][1]).json()
    assert catalog["items"][0]["id"] == SOURCE
    assert set_weather(client, hardware[0], users[1], a["revision"]).status_code == 404
    assert (
        set_weather(client, hardware[0], users[0], a["revision"], source_id="https://127.0.0.1").status_code
        == 422
    )
    assert (
        set_weather(
            client, hardware[0], users[0], a["revision"], params={**PARAMS, "url": "https://evil.test"}
        ).status_code
        == 422
    )
    assert (
        set_weather(
            client, hardware[0], users[0], a["revision"], params={"latitude": 91, "longitude": 0}
        ).status_code
        == 422
    )
    assert set_weather(client, hardware[0], users[0], a["revision"]).status_code == 200
    assert set_weather(client, hardware[0], users[0], a["revision"]).status_code == 409
    path = f"/api/v1/me/devices/{hardware[0][0]}/widgets"
    assert client.get(path, headers=users[1][1]).status_code == 404
    data = client.get(path, headers=users[0][1]).json()
    assert data["items"][0]["params"] == PARAMS
    assert data["revision"] == a["revision"] + 1
    assert client.delete(f"/api/v1/me/devices/{hardware[0][0]}", headers=users[0][1]).status_code == 204
    db = app.state.db
    assert find_model(db, "WidgetBinding", (hardware[0][0], "weather")) is None
    assert find_model(db, "Device", hardware[1][0]).owner_id == users[1][0]


def test_shared_canonical_cache_deduplicates_and_fresh_cache_skips_fetch(
    app, client, users, hardware, claim, monkeypatch
):
    calls = []
    for device, user in zip(hardware, users):
        state = claim(device, user)
        assert (
            set_weather(
                client,
                device,
                user,
                state["revision"],
                params={"longitude": 108.202200001, "latitude": 16.054400001},
            ).status_code
            == 200
        )

    def fetch(latitude, longitude):
        calls.append((latitude, longitude))
        return Weather(temp=32, feels=34, hi=33, lo=26, hum=70, wind=10, c=0)

    monkeypatch.setattr("lumi_be.providers.weather.fetch_weather", fetch)
    assert refresh(app.state.db) == {"updated": 1, "failed": 0}
    assert calls == [(16.0544, 108.2022)]
    assert refresh(app.state.db) == {"updated": 0, "failed": 0}
    db = app.state.db
    assert db.provider_cache.count_documents({}) == 1
    for device in hardware:
        response = client.get("/api/v1/device/widgets/weather", headers=device[1])
        assert response.status_code == 200 and response.json()["data"]["temp"] == 32
        assert response.json()["state"] == "fresh" and len(response.content) <= 512
        assert "source_id" not in response.text and "latitude" not in response.text


def test_widget_etag_staleness_expiry_pending_and_disabled(app, client, users, hardware, claim):
    state = claim(hardware[0], users[0])
    path = "/api/v1/device/widgets/weather"
    assert client.get(path, headers=hardware[0][1]).json()["state"] == "pending"
    db = app.state.db
    insert_model(
        db, ProviderCache(key=weather_key(**PARAMS), payload={"temp": 30}, updated_at=int(time.time()))
    )
    response = client.get(path, headers=hardware[0][1])
    headers = {**hardware[0][1], "If-None-Match": response.headers["ETag"]}
    assert client.get(path, headers=headers).status_code == 304
    db = app.state.db
    find_model(db, "ProviderCache", weather_key(**PARAMS)).updated_at = int(time.time()) - 4000
    response = client.get(path, headers=headers)
    assert response.status_code == 200 and response.json()["state"] == "stale"
    db = app.state.db
    find_model(db, "ProviderCache", weather_key(**PARAMS)).updated_at = int(time.time()) - 86400
    assert client.get(path, headers=hardware[0][1]).json()["data"] is None
    assert set_weather(client, hardware[0], users[0], state["revision"], enabled=False).status_code == 200
    assert client.get(path, headers=hardware[0][1]).json()["state"] == "unconfigured"
    assert client.get("/api/v1/device/snapshot", headers=hardware[0][1]).json()["weather"] is None
    assert client.get(path, headers=hardware[1][1]).json()["data"] is None


def test_hidden_or_disabled_widgets_do_not_fetch(app, client, users, hardware, claim, monkeypatch):
    state = claim(hardware[0], users[0])
    assert set_weather(client, hardware[0], users[0], state["revision"]).status_code == 200
    db = app.state.db
    find_model(db, "Device", hardware[0][0]).layout = {
        "version": 1,
        "widgets": [{"id": "weather", "type": "weather", "visible": False}],
    }

    def unexpected(*args, **kwargs):
        raise AssertionError("Hidden widget must not consume an upstream request")

    monkeypatch.setattr("lumi_be.providers.weather.fetch_weather", unexpected)
    assert refresh(app.state.db) == {"updated": 0, "failed": 0}
    assert set_weather(client, hardware[0], users[0], state["revision"] + 1, enabled=False).status_code == 200
    db = app.state.db
    find_model(db, "Device", hardware[0][0]).layout = None
    assert refresh(app.state.db) == {"updated": 0, "failed": 0}


def test_canonical_key_includes_source_and_parameters():
    assert cache_key(SOURCE, PARAMS) == cache_key(SOURCE, dict(reversed(list(PARAMS.items()))))
    assert cache_key(SOURCE, PARAMS) != cache_key(SOURCE, {**PARAMS, "latitude": 10})
