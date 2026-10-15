"""Read-only cloud plane. No device identity, user settings or location history."""

import json
import time
from typing import Literal

import httpx
from fastapi import APIRouter, Query, Request
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ..db import utc_date
from ..deps import DB
from ..errors import fail
from ..models import Asset
from ..providers.registry import SOURCES, cache_key, fetch, normalize
from ..services import asset_app_view, limit, remote, wire

router = APIRouter(tags=["Public data v1"])


@router.get("/api/v1/widget-sources")
def sources():
    return {
        "schema": 1,
        "items": [
            {
                "id": source.id,
                "widget_type": source.widget_type,
                "schema": 1,
                "ttl_seconds": source.ttl,
                "parameters": source.params.model_json_schema(),
            }
            for source in SOURCES.values()
        ],
    }


@router.get("/api/v1/widgets/weather")
def weather(
    request: Request,
    db: DB,
    latitude: float = Query(default=16.0544, ge=-90, le=90, allow_inf_nan=False),
    longitude: float = Query(default=108.2022, ge=-180, le=180, allow_inf_nan=False),
):
    limit(request, f"weather-read:{remote(request)}", 120, seconds=3600)
    source = SOURCES["open-meteo-weather-v1"]
    # Coarse public cache (~1 km); no device/user ID or movement history is stored.
    params = normalize(source.id, {"latitude": round(latitude, 2), "longitude": round(longitude, 2)})
    key = cache_key(source.id, params)
    now = int(time.time())
    row = db.provider_cache.find_one({"_id": key})
    fresh = row and row.get("payload") and 0 <= now - row["updated_at"] < source.ttl
    if not fresh:
        if not row and db.provider_cache.count_documents({}, limit=10000) >= 10000:
            fail(503, "cache_capacity", "Data cache is busy; keep the local fallback")
        # Unique key + short lease prevents simultaneous cache misses stampeding upstream.
        try:
            acquired = db.provider_cache.find_one_and_update(
                {
                    "_id": key,
                    "lease_until": {"$not": {"$gt": now}},
                    "$or": [{"updated_at": {"$lte": now - source.ttl}}, {"updated_at": {"$gt": now}}],
                },
                {
                    "$set": {"lease_until": now + 20},
                    "$setOnInsert": {
                        "source_id": source.id,
                        "payload": {},
                        "updated_at": 0,
                        "expire_at": utc_date(now + source.max_age),
                    },
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            acquired = None
        if acquired:
            try:
                limit(request, "weather-upstream-global", 30, seconds=60)
                payload = fetch(source.id, params)
                if len(json.dumps(payload).encode()) > 256:
                    raise ValueError("Provider payload exceeds its budget")
                db.provider_cache.update_one(
                    {"_id": key},
                    {
                        "$set": {
                            "payload": payload,
                            "updated_at": now,
                            "lease_until": 0,
                            "expire_at": utc_date(now + source.max_age),
                        }
                    },
                )
                row = {"payload": payload, "updated_at": now}
            except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
                # Retain stale valid data and leave the lease as a bounded retry delay.
                pass
        else:
            row = db.provider_cache.find_one({"_id": key})
    usable = row and row.get("payload") and 0 <= now - row["updated_at"] < source.max_age
    return wire(
        {
            "v": 1,
            "t": "weather",
            "schema": 1,
            "state": ("fresh" if now - row["updated_at"] < source.ttl else "stale") if usable else "pending",
            "at": row["updated_at"] if usable else None,
            "retry_seconds": source.ttl if usable else 30,
            "data": row["payload"] if usable else None,
        }
    )


@router.get("/api/v1/assets")
def assets(
    request: Request,
    db: DB,
    model: Literal["WazeHUD-CYD"] = "WazeHUD-CYD",
    offset: int = Query(default=0, ge=0, le=100000),
):
    limit(request, f"asset-catalogue:{remote(request)}", 120, seconds=3600)
    # Never expose images from former accounts through the public catalogue.
    rows = db.assets.find({"model": model, "owner_id": None}).sort("_id", 1).skip(offset).limit(50)
    return {
        "schema": 1,
        "items": [asset_app_view(Asset.model_validate(row)) for row in rows],
        "next_offset": offset + 50,
    }
