import time
from uuid import UUID

from fastapi import APIRouter, Request
from pymongo import ReturnDocument

from ..deps import DB, CurrentUser
from ..errors import fail
from ..models import Asset, Device
from ..schemas import Claim, ConfigWrite, LayoutWrite, Navigation
from ..security import digest
from ..services import (
    asset_view,
    compact,
    config_for,
    limit,
    mutate,
    owned,
    remote,
    visible_asset,
)

router = APIRouter(prefix="/api/v1/me/devices", tags=["Companion cloud control"])


def view(device):
    return {
        "id": device.id,
        "serial": device.serial,
        "model": device.model,
        "firmware": device.firmware,
        "build": device.firmware_build,
        "revision": device.revision,
        "applied_revision": device.applied_revision,
        "last_seen": device.last_seen,
        "online": 0 <= int(time.time()) - device.last_seen < 90,
        "enabled": device.enabled,
    }


def offset_check(offset):
    if offset < 0 or offset > 100000:
        fail(422, "invalid_offset", "Invalid pagination offset")


@router.get("")
def devices(user: CurrentUser, db: DB, offset: int = 0):
    offset_check(offset)
    rows = db.devices.find({"owner_id": user.id}).sort("_id", 1).skip(offset).limit(50)
    return {"items": [view(Device.model_validate(row)) for row in rows], "next_offset": offset + 50}


@router.post("/claim")
def claim(body: Claim, request: Request, user: CurrentUser, db: DB):
    limit(request, f"claim-user:{user.id}", 10)
    limit(request, f"claim-ip:{remote(request)}", 20)
    row = db.devices.find_one_and_update(
        {
            "_id": str(body.device_id),
            "owner_id": None,
            "enabled": True,
            "revision": {"$lt": 2147483647},
            "pairing_hash": digest(body.code),
            "pairing_expires_at": {"$gt": int(time.time())},
        },
        {
            "$set": {"owner_id": user.id, "pairing_hash": None, "pairing_expires_at": 0},
            "$inc": {"revision": 1},
        },
        return_document=ReturnDocument.AFTER,
    )
    if not row:
        fail(404, "claim_invalid", "Pairing code is invalid, expired or already used")
    return view(Device.model_validate(row))


@router.get("/{device_id}")
def details(device_id: UUID, user: CurrentUser, db: DB):
    device = owned(db, user.id, device_id)
    return {**view(device), "config": config_for(device).model_dump(mode="json"), "layout": device.layout}


@router.delete("/{device_id}", status_code=204)
def unbind(device_id: UUID, user: CurrentUser, db: DB):
    device = owned(db, user.id, device_id)
    changed = db.devices.update_one(
        {"_id": device.id, "owner_id": user.id, "revision": {"$lt": 2147483647}},
        {
            "$set": {
                "owner_id": None,
                "pairing_hash": None,
                "pairing_expires_at": 0,
                "config": {},
                "layout": None,
                "widgets": {},
                "navigation": None,
                "navigation_at_ms": 0,
                "applied_revision": 0,
            },
            "$inc": {"revision": 1},
        },
    )
    if changed.matched_count != 1:
        fail(409, "ownership_changed", "Device ownership changed or revision exhausted")


@router.put("/{device_id}/config")
def set_config(device_id: UUID, body: ConfigWrite, user: CurrentUser, db: DB):
    device = owned(db, user.id, device_id)
    if body.config.asset_id:
        visible_asset(db, device, body.config.asset_id)
    return mutate(db, user.id, device, body.expected_revision, config=body.config.model_dump(mode="json"))


@router.put("/{device_id}/layout")
def set_layout(device_id: UUID, body: LayoutWrite, user: CurrentUser, db: DB):
    device = owned(db, user.id, device_id)
    return mutate(db, user.id, device, body.expected_revision, layout=body.layout.model_dump(mode="json"))


@router.post("/{device_id}/navigation", status_code=202)
def navigation(device_id: UUID, body: Navigation, request: Request, user: CurrentUser, db: DB):
    device = owned(db, user.id, device_id)
    payload = {"v": 1, "t": "s", **body.model_dump(exclude={"v", "t"}, exclude_defaults=True)}
    if len(compact(payload)) + 1 > 512:
        fail(422, "frame_too_large", "Navigation exceeds the 512-byte HLP contract")
    limit(request, f"navigation:{user.id}:{device.id}", 20, seconds=1)
    changed = db.devices.update_one(
        {"_id": device.id, "owner_id": user.id, "enabled": True},
        {"$set": {"navigation": payload, "navigation_at_ms": int(time.time() * 1000)}},
    )
    if changed.matched_count != 1:
        fail(409, "ownership_changed", "Device ownership changed")
    return {"accepted": True, "ttl_ms": 3000}


@router.get("/{device_id}/assets")
def assets(device_id: UUID, user: CurrentUser, db: DB, offset: int = 0):
    device = owned(db, user.id, device_id)
    offset_check(offset)
    rows = (
        db.assets.find({"model": device.model, "$or": [{"owner_id": None}, {"owner_id": user.id}]})
        .sort("_id", 1)
        .skip(offset)
        .limit(50)
    )
    return {"items": [asset_view(Asset.model_validate(row)) for row in rows], "next_offset": offset + 50}
