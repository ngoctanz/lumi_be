import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError, OperationFailure

from lumi_be.db import initialize, ready, utc_date
from lumi_be.models import Device
from lumi_be.services import limit


def test_indexes_idempotent_nullable_pairing_and_unique_keys(app, hardware):
    db = app.state.db
    initialize(db)
    initialize(db)
    assert ready(db)
    assert db.devices.count_documents({"pairing_hash": None}) == 2
    row = Device(serial="CYD-A", token_hash="other-token", created_at=int(time.time())).mongo()
    with pytest.raises(DuplicateKeyError):
        db.devices.insert_one(row)
    db.devices.update_one({"_id": hardware[0][0]}, {"$set": {"pairing_hash": "same-code-hash"}})
    with pytest.raises(DuplicateKeyError):
        db.devices.update_one({"_id": hardware[1][0]}, {"$set": {"pairing_hash": "same-code-hash"}})
    for collection, name in [
        ("login_sessions", "ttl_sessions"),
        ("provider_cache", "ttl_provider_cache"),
        ("rate_limits", "ttl_rate_limits"),
    ]:
        assert db[collection].index_information()[name]["expireAfterSeconds"] == 0


def test_database_validator_rejects_bad_widget_and_revision(app, hardware):
    db = app.state.db
    for values in (
        {"revision": 0},
        {"revision": True},
        {"widgets.bad-widget": {}},
        {"widgets.weather": {"enabled": True}},
    ):
        with pytest.raises(OperationFailure) as failure:
            db.devices.update_one({"_id": hardware[0][0]}, {"$set": values})
        assert failure.value.code == 121


def test_rate_limit_concurrent_increment_never_exceeds_allowance(app):
    request = SimpleNamespace(app=app)

    def compete(_):
        try:
            limit(request, "concurrent-storage-test", 5, seconds=86400)
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        statuses = list(pool.map(compete, range(30)))
    assert statuses.count(200) == 5
    assert statuses.count(429) == 25


def test_expired_session_refused_before_ttl_monitor_runs(app, client, users):
    from lumi_be.security import digest

    auth = users[0][1]
    key = digest(auth["Authorization"].removeprefix("Bearer "))
    app.state.db.login_sessions.update_one(
        {"_id": key},
        {"$set": {"expires_at": int(time.time()) - 1, "expire_at": utc_date(time.time() + 3600)}},
    )
    assert app.state.db.login_sessions.find_one({"_id": key}) is not None
    assert client.get("/api/v1/auth/me", headers=auth).status_code == 401
