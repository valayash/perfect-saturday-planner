"""Geocoding (Open-Meteo, free, no key) and distance/travel helpers."""

from __future__ import annotations

import asyncio
import math
import re

import httpx2

from .models import Location
from .pricing import travel_rates

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
PHOTON_URL = "https://photon.komoot.io/api/"
USER_AGENT = "perfect-saturday-planner/1.0 (take-home demo)"

# Common alternate names the geocoder handles poorly.
ALIASES = {"bangalore": "Bengaluru", "bombay": "Mumbai", "madras": "Chennai", "calcutta": "Kolkata", "gurgaon": "Gurugram"}

_cache: dict[tuple[str, str | None], list[dict]] = {}

# Popular neighbourhoods, so common areas resolve instantly and to the right side of town
# (e.g. the "Bandra" in geocoders is Bandra East, but people mean the Bandra West of Carter Road).
AREA_ANCHORS: dict[str, dict[str, tuple[float, float]]] = {
    "bengaluru": {
        "indiranagar": (12.9719, 77.6412), "koramangala": (12.9352, 77.6245), "mg road": (12.9756, 77.6066),
        "church street": (12.9750, 77.6046), "jayanagar": (12.9250, 77.5938), "whitefield": (12.9698, 77.7500),
        "hsr layout": (12.9116, 77.6474), "malleshwaram": (13.0035, 77.5710), "basavanagudi": (12.9417, 77.5755),
        "jp nagar": (12.9063, 77.5857), "hebbal": (13.0358, 77.5970), "electronic city": (12.8452, 77.6602),
    },
    "mumbai": {
        "bandra": (19.0596, 72.8295), "bandra west": (19.0596, 72.8295), "colaba": (18.9067, 72.8147), "fort": (18.9330, 72.8340),
        "juhu": (19.1075, 72.8263), "andheri west": (19.1364, 72.8296), "andheri": (19.1197, 72.8468), "lower parel": (18.9966, 72.8295),
        "powai": (19.1176, 72.9060), "dadar": (19.0178, 72.8478), "churchgate": (18.9322, 72.8264), "kala ghoda": (18.9282, 72.8315),
    },
    "delhi": {
        "khan market": (28.6002, 77.2270), "hauz khas": (28.5494, 77.2001), "connaught place": (28.6315, 77.2167), "cp": (28.6315, 77.2167),
        "saket": (28.5245, 77.2066), "lodhi colony": (28.5850, 77.2230), "chandni chowk": (28.6506, 77.2303),
        "greater kailash": (28.5482, 77.2347), "vasant kunj": (28.5200, 77.1590),
    },
    "pune": {"koregaon park": (18.5362, 73.8940), "kalyani nagar": (18.5463, 73.9033), "baner": (18.5590, 73.7868)},
    "gurugram": {"cyber hub": (28.4950, 77.0890), "cyber city": (28.4950, 77.0890)},
}
CITY_ANCHOR_KEYS = {"bengaluru": "bengaluru", "bangalore": "bengaluru", "mumbai": "mumbai", "bombay": "mumbai", "delhi": "delhi",
                    "new delhi": "delhi", "pune": "pune", "gurugram": "gurugram", "gurgaon": "gurugram"}


def _anchor(city: Location, area: str) -> Location | None:
    key = CITY_ANCHOR_KEYS.get(city.name.strip().lower())
    hit = AREA_ANCHORS.get(key or "", {}).get(re.sub(r"\s+", " ", area.strip().lower()))
    if not hit:
        return None
    name = area.strip().title()
    return city.model_copy(update={"name": name, "lat": hit[0], "lon": hit[1], "label": f"{name}, {city.label}"})


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

        area_loc = _anchor(city_loc, area) if area else None
        if area and not area_loc:
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
    features: list = []
    for attempt in range(3):  # the public Photon server briefly 503s under bursts
        try:
            resp = await client.get(PHOTON_URL, params={"q": area, "lat": city.lat, "lon": city.lon, "limit": 5, "lang": "en"}, timeout=5.0)
            if resp.status_code in (429, 503) and attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            resp.raise_for_status()
            features = resp.json().get("features", [])
            break
        except (httpx2.HTTPError, ValueError):
            if attempt == 2:
                return None
            await asyncio.sleep(0.5)
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
