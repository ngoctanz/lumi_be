import time
from concurrent.futures import ThreadPoolExecutor

from mongo_fixtures import find_model

from lumi_be.security import digest


def test_accounts_login_logout_and_token_scope(client, users, hardware):
    (_, auth), _ = users
    assert client.get("/api/v1/auth/me", headers=auth).status_code == 200
    assert client.get("/api/v1/device/snapshot", headers=auth).status_code == 401
    assert client.get("/api/v1/auth/me", headers=hardware[0][1]).status_code == 401
    invalid = client.post(
        "/api/v1/auth/login", json={"email": "missing@example.test", "password": "correct-horse-42"}
    )
    assert invalid.status_code == 401
    logged = client.post(
        "/api/v1/auth/login", json={"email": "OWNER-A@example.test", "password": "correct-horse-42"}
    )
    assert logged.status_code == 200
    assert client.post("/api/v1/auth/logout", headers=auth).status_code == 204
    assert client.get("/api/v1/auth/me", headers=auth).status_code == 401


def test_expired_sessions_and_revoked_device_keys(app, client, users, hardware):
    (_, auth), _ = users
    device_id, device_auth = hardware[0]
    db = app.state.db
    session = find_model(db, "LoginSession", digest(auth["Authorization"].removeprefix("Bearer ")))
    session.expires_at = int(time.time()) - 1
    find_model(db, "Device", device_id).enabled = False
    assert client.get("/api/v1/auth/me", headers=auth).status_code == 401
    assert client.get("/api/v1/device/snapshot", headers=device_auth).status_code == 401


def test_claim_requires_code_for_exact_device_and_is_one_time(client, users, hardware):
    (id_a, auth_a), (id_b, _) = hardware
    code = client.post("/api/v1/device/pairing", headers=auth_a).json()["code"]
    payload = {"device_id": id_b, "code": code}
    assert client.post("/api/v1/me/devices/claim", headers=users[0][1], json=payload).status_code == 404
    payload["device_id"] = id_a
    assert client.post("/api/v1/me/devices/claim", headers=users[0][1], json=payload).status_code == 200
    assert client.post("/api/v1/me/devices/claim", headers=users[1][1], json=payload).status_code == 404
    assert client.post("/api/v1/device/pairing", headers=auth_a).status_code == 409
    assert client.get(f"/api/v1/me/devices/{id_a}", headers=users[1][1]).status_code == 404
    assert client.get("/api/v1/me/devices", headers=users[1][1]).json()["items"] == []


def test_pairing_expiry_and_server_hash_storage(app, client, users, hardware):
    device_id, auth = hardware[0]
    code = client.post("/api/v1/device/pairing", headers=auth).json()["code"]
    db = app.state.db
    device = find_model(db, "Device", device_id)
    assert device.pairing_hash != code
    assert device.pairing_hash == digest(code)
    device.pairing_expires_at = int(time.time()) - 1
    response = client.post(
        "/api/v1/me/devices/claim", headers=users[0][1], json={"device_id": device_id, "code": code}
    )
    assert response.status_code == 404


def test_concurrent_claim_has_exactly_one_owner(app, client, users, hardware):
    device_id, auth = hardware[0]
    code = client.post("/api/v1/device/pairing", headers=auth).json()["code"]

    def compete(account):
        parallel = client
        return parallel.post(
            "/api/v1/me/devices/claim", headers=account[1], json={"device_id": device_id, "code": code}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, users))
    assert sorted(results) == [200, 404]
    db = app.state.db
    assert find_model(db, "Device", device_id).owner_id in {user[0] for user in users}


def test_claim_rate_limit(client, users, hardware):
    for _ in range(10):
        assert (
            client.post(
                "/api/v1/me/devices/claim",
                headers=users[0][1],
                json={"device_id": hardware[0][0], "code": "AAAAAAAAAA"},
            ).status_code
            == 404
        )
    response = client.post(
        "/api/v1/me/devices/claim",
        headers=users[0][1],
        json={"device_id": hardware[0][0], "code": "AAAAAAAAAA"},
    )
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


def test_unbind_resets_only_owned_device_and_allows_new_owner(app, client, users, hardware, claim):
    claim(hardware[0], users[0])
    claim(hardware[1], users[1])
    id_a, auth_a = hardware[0]
    id_b, _ = hardware[1]
    assert client.delete(f"/api/v1/me/devices/{id_a}", headers=users[1][1]).status_code == 404
    assert client.delete(f"/api/v1/me/devices/{id_a}", headers=users[0][1]).status_code == 204
    db = app.state.db
    assert find_model(db, "Device", id_a).owner_id is None
    assert find_model(db, "Device", id_a).config == {}
    assert db.devices.find_one({"_id": id_b})["owner_id"] == users[1][0]
    claim((id_a, auth_a), users[1])
