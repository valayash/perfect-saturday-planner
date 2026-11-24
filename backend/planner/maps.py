"""Route map images: Geoapify Static Maps behind a signed /api/map URL.

The plan carries a URL to our own API, signed with a secret derived from GEOAPIFY_API_KEY, so the key stays on
the server and the endpoint only draws maps for plans this server built (it can't be used as an open map proxy).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from urllib.parse import quote

import httpx2

STATIC_MAP_URL = "https://maps.geoapify.com/v1/staticmap"
MAX_POINTS = 8  # start + up to 7 stops
STOP_COLOR, START_COLOR = "#ea580c", "#475569"
CACHE_S, CACHE_MAX = 3600, 64
_cache: dict[str, tuple[float, bytes, str]] = {}


def _secret() -> bytes | None:
    key = os.environ.get("GEOAPIFY_API_KEY")
    return hashlib.sha256(f"route-map:{key}".encode()).digest() if key else None


def _sign(data: str, secret: bytes) -> str:
    return hmac.new(secret, data.encode(), hashlib.sha256).hexdigest()[:24]


def map_path(start: tuple[float, float], stops: list[tuple[float, float]]) -> str | None:
    """Signed /api/map URL for the start point and the stops in order, or None without a Geoapify key."""
    secret = _secret()
    if not secret or not stops:
        return None
    data = ";".join(f"{lat:.5f},{lon:.5f}" for lat, lon in [start, *stops[: MAX_POINTS - 1]])
    return f"/api/map?d={quote(data, safe=',;')}&sig={_sign(data, secret)}"


def parse_points(data: str, sig: str) -> list[tuple[float, float]] | None:
    """The points of a signed map URL, or None if the signature or the points are invalid."""
    secret = _secret()
    if not secret or not hmac.compare_digest(_sign(data, secret), sig):
        return None
    try:
        pts = [(float(a), float(b)) for a, b in (p.split(",") for p in data.split(";"))]
    except ValueError:
        return None
    if not 2 <= len(pts) <= MAX_POINTS or not all(-90 <= lat <= 90 and -180 <= lon <= 180 for lat, lon in pts):
        return None
    return pts


async def render(points: list[tuple[float, float]]) -> tuple[bytes, str]:
    """The map image: a pin for the user's start area, numbered stops, dashed straight lines between them."""
    key = ";".join(f"{lat:.5f},{lon:.5f}" for lat, lon in points)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1], hit[2]

    start, stops = points[0], points[1:]
    lats, lons = [p[0] for p in points], [p[1] for p in points]
    pad_lat = max(0.004, (max(lats) - min(lats)) * 0.2)
    pad_lon = max(0.004, (max(lons) - min(lons)) * 0.2)
    # Extra room at the top: pins stand above their point and would be cut off at the edge.
    area = f"rect:{min(lons) - pad_lon:.5f},{max(lats) + pad_lat * 1.8:.5f},{max(lons) + pad_lon:.5f},{min(lats) - pad_lat:.5f}"
    markers = [
        f"lonlat:{lon},{lat};type:material;color:{STOP_COLOR};size:x-large;text:{i};contentsize:18;whitecircle:no"
        for i, (lat, lon) in enumerate(stops, 1)
    ]
    # A round badge drawn last, on the point itself: still visible when the first stop's pin stands right above it.
    markers.append(f"lonlat:{start[1]},{start[0]};type:circle;color:{START_COLOR};size:30;icon:home;icontype:material;contentsize:16;shadow:no")
    line = "polyline:" + ",".join(f"{lon},{lat}" for lat, lon in points) + f";linecolor:{STOP_COLOR};linewidth:3;linestyle:dashed;lineopacity:0.9"
    params = {
        "style": "osm-bright-grey", "width": 600, "height": 340, "scaleFactor": 2, "area": area,
        "marker": "|".join(markers), "geometry": line, "apiKey": os.environ.get("GEOAPIFY_API_KEY", ""),
    }
    async with httpx2.AsyncClient() as client:
        resp = await client.get(STATIC_MAP_URL, params=params, timeout=15.0)
    resp.raise_for_status()
    image, ctype = resp.content, resp.headers.get("content-type", "image/jpeg")
    if len(_cache) >= CACHE_MAX:
        _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
    _cache[key] = (time.monotonic(), image, ctype)
    return image, ctype
