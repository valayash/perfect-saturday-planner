"""Saturday forecast from Open-Meteo (free, no key)."""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx2

from .models import Location

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CACHE_S = 1800
_cache: dict[tuple, tuple[float, dict]] = {}

WMO = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "rain showers", 81: "rain showers", 82: "violent showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail",
}


def next_saturday(tz: str) -> date:
    try:
        today = datetime.now(ZoneInfo(tz)).date()
    except Exception:
        today = datetime.utcnow().date()
    return today + timedelta(days=(5 - today.weekday()) % 7)


async def saturday_forecast(loc: Location, start_hhmm: str, minutes: int) -> dict:
    day = next_saturday(loc.timezone)
    params = {
        "latitude": loc.lat,
        "longitude": loc.lon,
        "hourly": "temperature_2m,precipitation_probability,weather_code",
        "daily": "sunset,precipitation_probability_max,temperature_2m_max,temperature_2m_min",
        "timezone": "auto",
        "start_date": day.isoformat(),
        "end_date": day.isoformat(),
    }
    key = (round(loc.lat, 2), round(loc.lon, 2), day.isoformat())
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_S:
        data = cached[1]
    else:
        data = None
        async with httpx2.AsyncClient() as client:
            for attempt in range(2):  # Open-Meteo occasionally returns a transient 503
                try:
                    resp = await client.get(FORECAST_URL, params=params, timeout=6.0)
                    resp.raise_for_status()
                    data = resp.json()
                    break
                except httpx2.HTTPError:
                    if attempt:
                        raise
                    await asyncio.sleep(0.8)
        _cache[key] = (time.monotonic(), data)

    start_h = int(start_hhmm.split(":")[0])
    end_h = min(23, start_h + max(1, round(minutes / 60)))
    hourly = data["hourly"]
    window = []
    for i, ts in enumerate(hourly["time"]):
        h = int(ts[11:13])
        if start_h <= h <= end_h:
            window.append({
                "hour": f"{h:02d}:00",
                "temp_c": round(hourly["temperature_2m"][i]),
                "rain_chance": hourly["precipitation_probability"][i] or 0,
                "sky": WMO.get(hourly["weather_code"][i], "unknown"),
            })
    max_rain = max((w["rain_chance"] for w in window), default=0)
    daily = data["daily"]
    sunset = (daily.get("sunset") or [None])[0]
    return {
        "date": day.isoformat(),
        "summary": _summary(window, max_rain),
        "max_rain_chance_in_window": max_rain,
        "temp_range_c": [round(daily["temperature_2m_min"][0]), round(daily["temperature_2m_max"][0])],
        "sunset": sunset[11:16] if sunset else None,
        "hourly": window,
    }


def _summary(window: list[dict], max_rain: int) -> str:
    if not window:
        return "No hourly data for that window."
    skies = {w["sky"] for w in window}
    temps = [w["temp_c"] for w in window]
    rain = "rain likely, plan indoor options" if max_rain >= 60 else "some chance of rain" if max_rain >= 30 else "low chance of rain"
    return f"{', '.join(sorted(skies))}; {min(temps)}–{max(temps)}°C; {rain} (max {max_rain}%)."
