import json
import time
from urllib.parse import urlparse

from fastapi import Request
from pymongo import ReturnDocument

from .db import utc_date
from .errors import fail
from .models import Asset, Device
from .schemas import DeviceConfig
from .security import digest


def compact(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def wire(value, max_size=512):
    if len(compact(value)) > max_size:
        fail(500, "response_limit", "Server response exceeds the firmware contract")
    return value


def limit(request: Request, scope: str, count: int, seconds=60):
    now = int(time.time())
    key = digest(scope)
    bucket = now // seconds
    # One document per scope/window; simultaneous upserts share the unique _id.
    row = request.app.state.db.rate_limits.find_one_and_update(
        {"_id": f"{key}:{bucket}"},
        {"$inc": {"hits": 1}, "$setOnInsert": {"expire_at": utc_date((bucket + 1) * seconds)}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    accepted = row["hits"] <= count
    if not accepted:
        from fastapi import HTTPException

        raise HTTPException(
            429,
            {"code": "rate_limited", "message": "Try again later"},
            headers={"Retry-After": str(seconds - now % seconds)},
        )


def remote(request):
    # Trust forwarded headers only when Uvicorn is configured for the known proxy.
    return request.client.host if request.client else "unknown"


def owned(db, user_id, device_id):
    document = db.devices.find_one({"_id": str(device_id), "owner_id": user_id})
    result = Device.model_validate(document) if document else None
    if not result:
        fail(404, "device_not_found", "Device not found")
    return result


def mutate(db, user_id, device, revision, **values):
    if revision >= 2147483647:
        fail(409, "revision_exhausted", "Contact the operator before updating this device")
    changed = db.devices.update_one(
        {"_id": device.id, "owner_id": user_id, "revision": revision, "enabled": True},
        {"$set": values, "$inc": {"revision": 1}},
    )
    if changed.matched_count != 1:
        fail(409, "revision_conflict", "Reload this device before applying changes")
    return {"revision": revision + 1}


def config_for(device):
    return DeviceConfig.model_validate(device.config or {})


def visible_asset(db, device, asset_id):
    document = db.assets.find_one(
        {
            "_id": str(asset_id),
            "model": device.model,
            "$or": [{"owner_id": None}, {"owner_id": device.owner_id}],
        }
    )
    asset = Asset.model_validate(document) if document else None
    if not asset or (asset.owner_id and not device.owner_id):
        fail(404, "asset_not_found", "Asset not found")
    return asset


def asset_view(asset):
    return {
        "id": asset.id,
        "model": asset.model,
        "name": asset.name,
        "url": asset.url,
        "sha256": asset.sha256,
        "size": asset.size,
        "width": asset.width,
        "height": asset.height,
        "encoding": asset.encoding,
    }


def validate_cdn(url: str, settings):
    parsed = urlparse(url)
    if (
        len(url) > 384
        or parsed.scheme != "https"
        or parsed.hostname not in settings.cdn_hosts
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Asset URL must be HTTPS on an explicitly configured CDN host")
    return url


def weather_key(latitude, longitude):
    from .providers.registry import cache_key

    return cache_key("open-meteo-weather-v1", {"latitude": latitude, "longitude": longitude})
