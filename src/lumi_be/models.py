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
    provider: str = "external"
    provider_id: str | None = None
    preview_url: str | None = None
    preview_provider_id: str | None = None
    source_format: str | None = None
    created_at: int = 0


class ProviderCache(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    key: str = Field(alias="_id")
    source_id: str = "open-meteo-weather-v1"
    payload: dict
    updated_at: int
