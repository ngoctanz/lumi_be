"""Trusted adapters live in code; DB stores IDs/validated parameters, never executable URLs."""

import hashlib
import json
from dataclasses import dataclass

from pydantic import Field

from ..schemas import Input


class WeatherParams(Input):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


@dataclass(frozen=True)
class Source:
    id: str
    widget_type: str
    params: type[Input]
    ttl: int
    max_age: int


SOURCES = {"open-meteo-weather-v1": Source("open-meteo-weather-v1", "weather", WeatherParams, 3600, 86400)}


def normalize(source_id, params):
    source = SOURCES[source_id]
    values = source.params.model_validate(params).model_dump()
    # Canonical resolution ~11m; equivalent requests share one cache entry.
    if source.widget_type == "weather":
        values = {k: round(float(v), 4) + 0.0 for k, v in values.items()}
    return values


def cache_key(source_id, params):
    document = json.dumps([source_id, normalize(source_id, params)], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(document.encode()).hexdigest()


def fetch(source_id, params):
    if source_id == "open-meteo-weather-v1":
        from .weather import fetch_weather

        return fetch_weather(**params).model_dump()
    raise ValueError("No adapter installed")
