import time
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse

from ..deps import DB, CurrentDevice
from ..errors import fail
from ..providers.widgets import binding_for, cached
from ..schemas import DeviceHello
from ..security import digest, pairing_code
from ..services import (
    asset_view,
    config_for,
    limit,
    visible_asset,
    wire,
)

router = APIRouter(prefix="/api/v1/device", tags=["Firmware HTTP v1"])


@router.post("/hello")
def hello(body: DeviceHello, device: CurrentDevice, db: DB):
    """Call at boot and every 60s; no caller-supplied device ID is accepted."""
    if body.applied_revision > device.revision:
        fail(422, "revision_invalid", "Applied revision exceeds the desired revision")
    changed = db.devices.update_one(
        {"_id": device.id, "enabled": True, "revision": device.revision},
        {
            "$set": {
                "firmware": body.firmware,
                "firmware_build": body.build,
                "applied_revision": body.applied_revision,
                "last_seen": int(time.time()),
            }
        },
    )
    if changed.matched_count != 1:
        fail(409, "revision_conflict", "Device changed; repeat hello")
    return wire(
        {
            "v": 1,
            "t": "hello",
            "id": device.id,
            "model": device.model,
            "paired": device.owner_id is not None,
            "rev": device.revision,
            "poll_ms": 30000,
            "limits": {"snapshot": 512, "layout": 4096, "asset": 153600, "image": 3145728},
        }
    )


@router.post("/pairing", status_code=201)
def pairing(request: Request, device: CurrentDevice, db: DB):
    """Firmware must call only after a local physical pairing action."""
    limit(request, f"pairing:{device.id}", 5)
    code = pairing_code()
    expires = int(time.time()) + request.app.state.settings.pairing_ttl_seconds
    updated = db.devices.update_one(
        {"_id": device.id, "owner_id": None, "enabled": True},
        {"$set": {"pairing_hash": digest(code), "pairing_expires_at": expires}},
    )
    if updated.matched_count != 1:
        fail(409, "already_paired", "The owner must unbind this device first")
    return wire({"v": 1, "t": "pair", "id": device.id, "code": code, "expires_at": expires})


@router.get("/snapshot")
def snapshot(device: CurrentDevice, db: DB):
    """Bounded full snapshot. Large layouts/assets are separate resources."""
    now = int(time.time())
    config = config_for(device)
    spec = binding_for(db, device, "weather")
    weather = cached(db, *spec, now=now) if spec else None
    payload = {
        "v": 1,
        "t": "snap",
        "time": now,
        "rev": device.revision,
        "paired": bool(device.owner_id),
        "poll_ms": 30000,
        "offline": {"keep": True, "nav_ttl_ms": 3000},
        "cfg": {
            "brightness": config.brightness,
            "show_eta": config.show_eta,
            "show_alerts": config.show_alerts,
            "theme": config.theme,
            "wallpaper": config.wallpaper,
        },
        "asset": str(config.asset_id) if config.asset_id else None,
        "layout": device.layout is not None,
        "weather": {**weather.payload, "at": weather.updated_at}
        if weather and now - weather.updated_at < 86400
        else None,
    }
    return wire(payload)


@router.get("/layout")
def layout(device: CurrentDevice, if_none_match: str | None = Header(default=None)):
    etag = f'"layout-{device.revision}"'
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(
        wire({"v": 1, "rev": device.revision, "layout": device.layout}, 4096), headers={"ETag": etag}
    )


@router.get("/navigation")
def navigation(device: CurrentDevice):
    age = int(time.time() * 1000) - device.navigation_at_ms
    if not device.owner_id or not device.navigation or age < 0 or age >= 3000:
        return Response(status_code=204)
    return JSONResponse(wire(device.navigation), headers={"X-Frame-Age-Ms": str(age)})


@router.get("/assets/{asset_id}")
def asset(asset_id: UUID, device: CurrentDevice, db: DB):
    return wire(asset_view(visible_asset(db, device, asset_id)), 1024)
