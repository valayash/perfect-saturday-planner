"""Geocoding (Open-Meteo, free, no key) and distance/travel helpers."""

from __future__ import annotations

import math

import httpx2

from .models import Location
from .pricing import travel_rates

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
PHOTON_URL = "https://photon.komoot.io/api/"
USER_AGENT = "perfect-saturday-planner/1.0 (take-home demo)"

# Common alternate names the geocoder handles poorly.
ALIASES = {"bangalore": "Bengaluru", "bombay": "Mumbai", "madras": "Chennai", "calcutta": "Kolkata", "gurgaon": "Gurugram"}

_cache: dict[tuple[str, str | None], list[dict]] = {}


class GeocodeError(Exception):
    pass


async def _search(client: httpx2.AsyncClient, name: str, country_code: str | None) -> list[dict]:
    key = (name.lower(), country_code)
    if key in _cache:
        return _cache[key]
    params = {"name": name, "count": 10, "language": "en", "format": "json"}
    if country_code:
        params["countryCode"] = country_code
    resp = await client.get(GEOCODE_URL, params=params, timeout=6.0)
    resp.raise_for_status()
    results = resp.json().get("results") or []
    _cache[key] = results
    return results


def _to_location(r: dict) -> Location:
    parts = [r.get("name"), r.get("admin1"), r.get("country")]
    return Location(
        name=r["name"],
        lat=r["latitude"],
        lon=r["longitude"],
        country_code=r.get("country_code", "").upper(),
        timezone=r.get("timezone") or "UTC",
        label=", ".join(p for p in parts if p),
    )


async def resolve_location(city: str, area: str | None, country_code: str | None) -> tuple[Location, Location | None]:
    """Return (city, area-or-None). Picks the most populous match for the city."""
    query = ALIASES.get(city.strip().lower(), city.strip())
    async with httpx2.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        results = await _search(client, query, country_code)
        if not results and country_code:
            results = await _search(client, query, None)
        if not results:
            raise GeocodeError(f"Couldn't find a city called '{city}'.")
        best = max(results, key=lambda r: (r.get("population") or 0))
        city_loc = _to_location(best)

        area_loc = None
        if area:
            try:
                candidates = await _search(client, area.strip(), city_loc.country_code or None)
            except httpx2.HTTPError:
                candidates = []
            near = [c for c in candidates if haversine_km(city_loc.lat, city_loc.lon, c["latitude"], c["longitude"]) < 35]
            if near:
                area_loc = _to_location(min(near, key=lambda c: haversine_km(city_loc.lat, city_loc.lon, c["latitude"], c["longitude"])))
            else:  # Open-Meteo only knows populated places; Photon also knows markets, landmarks and suburbs
                area_loc = await _photon_area(client, area.strip(), city_loc)
    return city_loc, area_loc


async def _photon_area(client: httpx2.AsyncClient, area: str, city: Location) -> Location | None:
    try:
        resp = await client.get(PHOTON_URL, params={"q": area, "lat": city.lat, "lon": city.lon, "limit": 5, "lang": "en"}, timeout=5.0)
        resp.raise_for_status()
        features = resp.json().get("features", [])
    except (httpx2.HTTPError, ValueError):
        return None
    for f in features:
        lon, lat = f["geometry"]["coordinates"]
        if haversine_km(city.lat, city.lon, lat, lon) < 35:
            name = f["properties"].get("name") or area
            return city.model_copy(update={"name": name, "lat": lat, "lon": lon, "label": f"{name}, {city.label}"})
    return None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def travel_leg(lat1: float, lon1: float, lat2: float, lon2: float, country_code: str) -> dict:
    """Estimate a leg between two points: walk if short, otherwise auto/cab."""
    road_km = haversine_km(lat1, lon1, lat2, lon2) * 1.35  # roads are not straight lines
    rates = travel_rates(country_code)
    if road_km <= 1.2:
        return {"mode": "walk", "km": round(road_km, 1), "minutes": max(3, round(road_km / 4.5 * 60)), "cost": 0}
    minutes = round(road_km / rates["kmh"] * 60) + rates["pickup_min"]
    cost = rates["base"] + rates["per_km"] * road_km
    return {"mode": rates["mode"], "km": round(road_km, 1), "minutes": minutes, "cost": round(cost)}
