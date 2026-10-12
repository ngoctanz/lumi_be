import time

import httpx

from ..db import utc_date
from ..models import Device, ProviderCache
from ..services import compact, config_for
from .registry import SOURCES, cache_key, fetch, normalize


def binding_for(db, device, widget_type):
    if not device.owner_id:
        return None
    return resolve_binding(device, widget_type, device.widgets.get(widget_type))


def resolve_binding(device, widget_type, binding):
    if binding:
        return (binding["source_id"], binding["params"]) if binding["enabled"] else None
    if widget_type == "weather":
        config = config_for(device)
        return "open-meteo-weather-v1", normalize(
            "open-meteo-weather-v1", {"latitude": config.latitude, "longitude": config.longitude}
        )
    return None


def cached(db, source_id, params, now=None):
    now = int(time.time()) if now is None else now
    row = db.provider_cache.find_one({"_id": cache_key(source_id, params)})
    if not row or now < row["updated_at"] or now - row["updated_at"] >= SOURCES[source_id].max_age:
        return None
    return ProviderCache.model_validate(row)


def refresh(db):
    """One scheduler; cached readers never call upstream. No joins/N+1 bindings."""
    now = int(time.time())
    requests = {}
    rows = db.devices.find({"enabled": True, "owner_id": {"$type": "string"}}).batch_size(100)
    for row in rows:
        device = Device.model_validate(row)
        types = (
            {w["type"] for w in device.layout["widgets"] if w.get("visible", True)}
            if device.layout
            else {"weather"} | set(device.widgets)
        )
        for kind in types:
            spec = binding_for(db, device, kind)
            if spec:
                source_id, params = spec
                requests[cache_key(source_id, params)] = (source_id, params)
    updated = failed = 0
    for key, (source_id, params) in requests.items():
        row = db.provider_cache.find_one({"_id": key}, {"updated_at": 1})
        if row and 0 <= now - row["updated_at"] < SOURCES[source_id].ttl:
            continue
        try:
            payload = fetch(source_id, params)
            if len(compact(payload)) > 256:
                raise ValueError("Adapter exceeds the widget data budget")
            db.provider_cache.update_one(
                {"_id": key},
                {
                    "$set": {
                        "source_id": source_id,
                        "payload": payload,
                        "updated_at": now,
                        "expire_at": utc_date(now + SOURCES[source_id].max_age),
                    }
                },
                upsert=True,
            )
            updated += 1
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            failed += 1
    return {"updated": updated, "failed": failed}
