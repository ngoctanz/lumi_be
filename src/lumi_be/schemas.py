from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PositiveRevision = Annotated[int, Field(strict=True, ge=1, le=2147483647)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(Input):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize(cls, value):
        return value.lower()


class Claim(Input):
    device_id: UUID
    code: str = Field(pattern=r"^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{10}$")


class DeviceHello(Input):
    firmware: str = Field(min_length=1, max_length=31, pattern=r"^[\x20-\x7e]+$")
    build: int = Field(strict=True, ge=0, le=2147483647)
    applied_revision: int = Field(strict=True, ge=0, le=2147483647, default=0)


class Widget(Input):
    id: str = Field(min_length=1, max_length=23, pattern=r"^[A-Za-z0-9_-]+$")
    type: Literal["clock", "weather", "calendar", "bot", "navigation", "stock"]
    x: int = Field(strict=True, ge=0, le=319)
    y: int = Field(strict=True, ge=0, le=239)
    width: int = Field(strict=True, ge=1, le=320)
    height: int = Field(strict=True, ge=1, le=240)
    visible: bool = Field(strict=True, default=True)

    @model_validator(mode="after")
    def geometry(self):
        minimum = {
            "clock": (88, 96),
            "weather": (160, 96),
            "calendar": (180, 96),
            "bot": (72, 72),
            "navigation": (190, 200),
            "stock": (160, 96),
        }
        w, h = minimum[self.type]
        if self.width < w or self.height < h or self.x + self.width > 320 or self.y + self.height > 240:
            raise ValueError("Widget violates the 320x240 firmware contract")
        return self


class Layout(Input):
    version: Literal[1] = 1
    widgets: list[Widget] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def unique(self):
        if len({w.id for w in self.widgets}) != len(self.widgets) or len(
            {w.type for w in self.widgets}
        ) != len(self.widgets):
            raise ValueError("Duplicate widget identity or type")
        return self


class DeviceConfig(Input):
    brightness: int = Field(strict=True, ge=10, le=100, multiple_of=5, default=70)
    show_eta: bool = Field(strict=True, default=True)
    show_alerts: bool = Field(strict=True, default=True)
    theme: Literal["auto", "day", "night"] = "auto"
    wallpaper: Literal["none", "sunset", "garden", "coast", "moon"] = "sunset"
    asset_id: UUID | None = None
    channel: Literal["stable", "beta"] = "stable"
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False, default=16.0544)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False, default=108.2022)


class ConfigWrite(Input):
    expected_revision: PositiveRevision
    config: DeviceConfig


class LayoutWrite(Input):
    expected_revision: PositiveRevision
    layout: Layout


class Alert(Input):
    k: int = Field(strict=True, ge=0, le=255)
    d: int = Field(strict=True, ge=-1, le=1000000)


class Navigation(Input):
    v: Literal[1] = 1
    t: Literal["s"] = "s"
    nav: int = Field(strict=True, ge=0, le=1, default=0)
    spd: int = Field(strict=True, ge=0, le=400, default=0)
    lim: int = Field(strict=True, ge=0, le=400, default=0)
    over: int = Field(strict=True, ge=0, le=1, default=0)
    trn: int = Field(strict=True, ge=0, le=255, default=0)
    trn2: int = Field(strict=True, ge=0, le=255, default=0)
    dst: int = Field(strict=True, ge=-1, le=1000000, default=-1)
    st: str = Field(max_length=40, default="")
    st2: str = Field(max_length=40, default="")
    eta: str = Field(max_length=8, default="")
    rmin: int = Field(strict=True, ge=0, le=100000, default=0)
    rm: int = Field(strict=True, ge=0, le=10000000, default=0)
    rkm: float = Field(ge=0, le=100000, allow_inf_nan=False, default=0)
    avg: int = Field(strict=True, ge=0, le=1, default=0)
    avgL: int = Field(strict=True, ge=0, le=1000000, default=0)
    avgR: int = Field(strict=True, ge=0, le=400, default=0)
    avgP: int = Field(strict=True, ge=0, le=100, default=0)
    alr: int = Field(strict=True, ge=0, le=255, default=0)
    alrD: int = Field(strict=True, ge=-1, le=1000000, default=-1)
    alrV: int = Field(strict=True, ge=0, le=400, default=0)
    alrs: list[Alert] = Field(max_length=4, default_factory=list)
    ts: int = Field(strict=True, ge=0, le=4294967295, default=0)

    @field_validator("st", "st2", "eta")
    @classmethod
    def clean_text(cls, value):
        if any(ord(c) < 32 for c in value) or len(value.encode("utf-8")) > 40:
            raise ValueError("Firmware string exceeds its byte buffer")
        return value
