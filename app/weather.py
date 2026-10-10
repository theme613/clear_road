"""Open-Meteo hourly rain + temperature for KL (free, no key), cached 1 hour."""
import time
from datetime import date as Date, timedelta
from typing import Optional

import requests

LAT, LON = 3.139, 101.687
URL = "https://api.open-meteo.com/v1/forecast"

# The one cached weather dict: date -> (fetched_at, {"rain": [48], "temp": [48]})
WEATHER_CACHE: dict[str, tuple[float, dict]] = {}


def fetch_forecast(day: Date) -> dict:
    """48 hourly values each: (day - 1) 00:00 .. day 23:00. Raises on any problem."""
    params = {
        "latitude": LAT, "longitude": LON, "hourly": "precipitation,temperature_2m",
        "timezone": "Asia/Kuala_Lumpur",
        "start_date": (day - timedelta(days=1)).isoformat(), "end_date": day.isoformat(),
    }
    resp = requests.get(URL, params=params, timeout=10)
    resp.raise_for_status()
    hourly = resp.json()["hourly"]
    rain, temp = hourly["precipitation"], hourly.get("temperature_2m") or []
    if len(rain) != 48:
        raise ValueError(f"expected 48 hourly values, got {len(rain)}")
    return {"rain": [float(v or 0.0) for v in rain],
            "temp": [None if v is None else float(v) for v in temp] or [None] * 48}


def get_weather(day: Date, override: Optional[float] = None) -> tuple[dict, str]:
    """({"rain": [48], "temp": [48]}, source). Source: override / open-meteo / fallback."""
    if override is not None:
        cached = WEATHER_CACHE.get(day.isoformat())
        temp = cached[1]["temp"] if cached else [None] * 48
        return {"rain": [max(override, 0.0)] * 48, "temp": temp}, "override"
    key = day.isoformat()
    cached = WEATHER_CACHE.get(key)
    if cached and time.time() - cached[0] < 3600:
        return cached[1], "open-meteo"
    try:
        data = fetch_forecast(day)
        WEATHER_CACHE[key] = (time.time(), data)
        return data, "open-meteo"
    except Exception as e:
        print(f"[weather] Open-Meteo failed ({e}); using 0 mm (fallback)")
        return {"rain": [0.0] * 48, "temp": [None] * 48}, "fallback"


def slot_rain(rain: list[float], hour: float) -> tuple[float, float]:
    """(rain in the slot hour, sum of the 3 hours before). Index 24 = day 00:00."""
    i = 24 + int(hour)
    return rain[i], sum(rain[i - 3:i])
