import json
import time
from urllib.parse import urlparse

from fastapi import Request
from pymongo import ReturnDocument

from .db import utc_date
from .errors import fail
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


def asset_app_view(asset):
    return {
        **asset_view(asset),
        "preview_url": asset.preview_url,
        "provider": asset.provider,
        "created_at": asset.created_at,
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
