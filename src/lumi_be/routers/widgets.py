import time
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header, Response
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError

from ..deps import DB, CurrentDevice, CurrentUser
from ..errors import fail
from ..providers.registry import SOURCES, normalize
from ..providers.widgets import binding_for, cached
from ..schemas import Input, PositiveRevision
from ..security import digest
from ..services import compact, mutate, owned, wire

router = APIRouter(tags=["Widget sources v1"])
WidgetType = Literal["clock", "weather", "calendar", "bot", "navigation", "stock"]


class BindingWrite(Input):
    expected_revision: PositiveRevision
    source_id: str = Field(min_length=1, max_length=48)
    params: dict = Field(default_factory=dict, max_length=8)
    enabled: bool = Field(strict=True, default=True)


@router.get("/api/v1/widget-sources")
def catalog(user: CurrentUser):
    return {
        "items": [
            {
                "id": s.id,
                "widget_type": s.widget_type,
                "schema": 1,
                "ttl_seconds": s.ttl,
                "max_stale_seconds": s.max_age,
                "parameters": s.params.model_json_schema(),
            }
            for s in SOURCES.values()
        ],
        "local_widgets": ["clock", "calendar", "navigation"],
        "unconfigured_widgets": ["stock", "bot"],
    }


@router.get("/api/v1/me/devices/{device_id}/widgets")
def bindings(device_id: UUID, user: CurrentUser, db: DB):
    device = owned(db, user.id, device_id)
    rows = device.widgets.items()
    return {
        "revision": device.revision,
        "items": [{"widget_type": kind, **binding} for kind, binding in rows],
        "weather_uses_device_coordinates_if_unconfigured": True,
    }


@router.put("/api/v1/me/devices/{device_id}/widgets/{widget_type}")
def set_binding(device_id: UUID, widget_type: WidgetType, body: BindingWrite, user: CurrentUser, db: DB):
    device = owned(db, user.id, device_id)
    source = SOURCES.get(body.source_id)
    if not source or source.widget_type != widget_type:
        fail(422, "source_invalid", "Select an installed source compatible with this widget")
    try:
        params = normalize(source.id, body.params)
    except ValidationError:
        fail(422, "source_parameters_invalid", "Parameters do not match the source schema")
    return mutate(
        db,
        user.id,
        device,
        body.expected_revision,
        **{f"widgets.{widget_type}": {"source_id": source.id, "params": params, "enabled": body.enabled}},
    )


@router.get("/api/v1/device/widgets/{widget_type}")
def widget_data(
    widget_type: WidgetType, device: CurrentDevice, db: DB, if_none_match: str | None = Header(default=None)
):
    spec = binding_for(db, device, widget_type)
    now = int(time.time())
    row = cached(db, *spec, now=now) if spec else None
    source = SOURCES[spec[0]] if spec else None
    state = (
        "unconfigured"
        if not spec
        else "pending"
        if not row
        else ("fresh" if now - row.updated_at < source.ttl else "stale")
    )
    payload = wire(
        {
            "v": 1,
            "t": "widget",
            "widget": widget_type,
            "schema": 1,
            "rev": device.revision,
            "state": state,
            "at": row.updated_at if row else None,
            "expires_at": row.updated_at + source.max_age if row else None,
            "data": row.payload if row else None,
        }
    )
    # Time-dependent state is part of the ETag, so fresh->stale invalidates a 304.
    etag = '"' + digest(compact(payload).decode()) + '"'
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(payload, headers={"ETag": etag})
