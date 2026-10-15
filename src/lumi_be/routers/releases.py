from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from ..errors import fail
from ..firmware_releases import FirmwareReleaseError
from ..services import limit, remote

router = APIRouter(prefix="/api/v1/firmware/releases", tags=["Firmware releases"])


@router.get("/latest")
def latest_release(
    request: Request,
    model: str = Query(min_length=1, max_length=48),
    channel: Literal["stable", "beta"] = "stable",
    current_build: int = Query(default=0, ge=0, le=2147483647),
):
    limit(request, f"firmware-check:{remote(request)}", 120, seconds=3600)
    try:
        return request.app.state.firmware_catalog.latest(model, channel, current_build)
    except FirmwareReleaseError:
        fail(503, "release_source_unavailable", "Firmware release source is unavailable")


@router.get("/assets/{asset_id}")
def download_release_asset(asset_id: int, request: Request):
    limit(request, f"firmware-download:{remote(request)}", 20, seconds=3600)
    if asset_id < 1:
        fail(404, "release_asset_not_found", "Firmware package was not found")
    try:
        response, name, size = request.app.state.firmware_catalog.open_package(asset_id)
    except FirmwareReleaseError:
        fail(503, "release_source_unavailable", "Firmware release source is unavailable")

    def content():
        try:
            yield from response.iter_bytes(65536)
        finally:
            response.close()

    return StreamingResponse(
        content(),
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(size),
            "Content-Disposition": f'attachment; filename="{name}"',
        },
    )
