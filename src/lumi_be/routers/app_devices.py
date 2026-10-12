import time
from uuid import UUID

from fastapi import APIRouter, File, Form, Request, UploadFile
from pymongo import ReturnDocument

from ..cloud_images import CloudImageError, prepare_image
from ..deps import DB, CurrentUser
from ..errors import fail
from ..models import Asset, Device
from ..schemas import Claim, ConfigWrite, LayoutWrite, Navigation
from ..security import digest
from ..services import (
    asset_app_view,
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
    return {"items": [asset_app_view(Asset.model_validate(row)) for row in rows], "next_offset": offset + 50}


@router.post("/{device_id}/assets", status_code=201)
def upload_asset(
    device_id: UUID,
    request: Request,
    user: CurrentUser,
    db: DB,
    name: str = Form(min_length=1, max_length=64),
    file: UploadFile = File(...),
):
    device = owned(db, user.id, device_id)
    name = name.strip()
    if not name or any(ord(character) < 32 for character in name):
        fail(422, "invalid_asset_name", "Asset name is invalid")
    limit(request, f"asset-upload:{user.id}", 10, seconds=3600)
    if db.assets.count_documents(
        {"owner_id": user.id}, limit=request.app.state.settings.image_asset_limit
    ) >= (request.app.state.settings.image_asset_limit):
        fail(409, "asset_limit_reached", "Delete an image before uploading another")

    payload = file.file.read(request.app.state.settings.image_upload_max_bytes + 1)
    if len(payload) > request.app.state.settings.image_upload_max_bytes:
        fail(413, "image_too_large", "Image exceeds the upload limit")
    try:
        prepared = prepare_image(payload)
    except ValueError as exc:
        fail(422, "invalid_image", str(exc))

    asset = Asset(
        owner_id=user.id,
        model=device.model,
        name=name,
        url="pending",
        sha256=prepared.sha256,
        size=len(prepared.raw),
        provider="cloudinary",
        source_format=prepared.source_format,
        created_at=int(time.time()),
    )
    try:
        stored = request.app.state.image_store.upload(user.id, asset.id, prepared)
    except CloudImageError:
        fail(503, "image_store_unavailable", "Image storage is unavailable")
    asset.url = stored.raw_url
    asset.provider_id = stored.raw_public_id
    asset.preview_url = stored.preview_url
    asset.preview_provider_id = stored.preview_public_id
    try:
        db.assets.insert_one(asset.mongo())
    except Exception:
        try:
            request.app.state.image_store.delete(asset)
        except CloudImageError:
            pass
        raise
    return asset_app_view(asset)


@router.delete("/{device_id}/assets/{asset_id}", status_code=204)
def delete_asset(device_id: UUID, asset_id: UUID, request: Request, user: CurrentUser, db: DB):
    owned(db, user.id, device_id)
    document = db.assets.find_one({"_id": str(asset_id), "owner_id": user.id})
    asset = Asset.model_validate(document) if document else None
    if not asset:
        fail(404, "asset_not_found", "Asset not found")
    if asset.provider != "cloudinary" or not asset.provider_id or not asset.preview_provider_id:
        fail(409, "asset_externally_managed", "This image is managed outside Cloudinary")
    if db.devices.find_one({"owner_id": user.id, "config.asset_id": str(asset_id)}):
        fail(409, "asset_in_use", "Remove this image from device configuration first")
    try:
        request.app.state.image_store.delete(asset)
    except CloudImageError:
        fail(503, "image_store_unavailable", "Image storage is unavailable")
    db.assets.delete_one({"_id": asset.id, "owner_id": user.id})
