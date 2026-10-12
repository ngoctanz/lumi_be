"""Typed documents; external IDs remain UUID strings, independent of Mongo ObjectId."""

import uuid

from pydantic import BaseModel, ConfigDict, Field


def uid():
    return str(uuid.uuid4())


class Document(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    id: str = Field(default_factory=uid, alias="_id")

    def mongo(self):
        return self.model_dump(by_alias=True)


class User(Document):
    email: str
    password_hash: str
    created_at: int


class Device(Document):
    serial: str
    token_hash: str
    model: str = "WazeHUD-CYD"
    enabled: bool = True
    owner_id: str | None = None
    pairing_hash: str | None = None
    pairing_expires_at: int = 0
    revision: int = 1
    config: dict = Field(default_factory=dict)
    layout: dict | None = None
    widgets: dict = Field(default_factory=dict)
    navigation: dict | None = None
    navigation_at_ms: int = 0
    firmware: str = "unknown"
    firmware_build: int = 0
    last_seen: int = 0
    applied_revision: int = 0
    created_at: int


class Asset(Document):
    owner_id: str | None = None
    model: str
    name: str
    url: str
    sha256: str
    size: int
    width: int = 320
    height: int = 240
    encoding: str = "rgb565-le"


class ProviderCache(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    key: str = Field(alias="_id")
    source_id: str = "open-meteo-weather-v1"
    payload: dict
    updated_at: int
