import math

import httpx
from pydantic import BaseModel, Field


class Weather(BaseModel):
    temp: int = Field(ge=-90, le=70)
    feels: int = Field(ge=-110, le=90)
    hi: int = Field(ge=-90, le=70)
    lo: int = Field(ge=-90, le=70)
    hum: int = Field(ge=0, le=100)
    wind: int = Field(ge=0, le=500)
    c: int = Field(ge=0, le=4)


def fetch_weather(latitude, longitude, transport=None):
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min",
        "forecast_days": 1,
        "timezone": "Asia/Ho_Chi_Minh",
    }
    # A fixed upstream avoids fetching arbitrary URLs supplied by phones/devices.
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False, transport=transport) as client:
        with client.stream(
            "GET",
            "https://api.open-meteo.com/v1/forecast",
            params=params,
            headers={"Accept-Encoding": "identity"},
        ) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 65536:
                    raise ValueError("Upstream weather response too large")
                chunks.append(chunk)
        import json

        document = json.loads(b"".join(chunks))
    current, daily = document["current"], document["daily"]
    code = current["weather_code"]
    if not isinstance(code, (float, int)) or not math.isfinite(code) or code < 0 or code > 99:
        raise ValueError("Invalid weather code")
    condition = 0 if code == 0 else 1 if code <= 2 else 4 if code >= 95 else 3 if 51 <= code <= 86 else 2
    values = [
        current["temperature_2m"],
        current["apparent_temperature"],
        daily["temperature_2m_max"][0],
        daily["temperature_2m_min"][0],
        current["relative_humidity_2m"],
        current["wind_speed_10m"],
    ]
    if any(not isinstance(v, (float, int)) or isinstance(v, bool) or not math.isfinite(v) for v in values):
        raise ValueError("Invalid weather measurements")
    return Weather(**dict(zip(["temp", "feels", "hi", "lo", "hum", "wind"], map(round, values))), c=condition)
