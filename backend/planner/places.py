"""Real places from OpenStreetMap, with curated and generic fallbacks.

Two live sources, queried in parallel:
  * Overpass: rich tags (opening hours, diet, cuisine) but the public servers are often at capacity.
  * Photon (komoot): fast OSM search with category filters, but names and locations only.
Overpass wins when it answers in time; otherwise Photon. If both fail, curated data, then generic.
"""

from __future__ import annotations

import asyncio
import math
import re
import time

import httpx2

from .fallback_data import curated_for
from .geo import USER_AGENT, haversine_km, photon_search
from .models import Location, Place
from .pricing import category_meta

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT = 1.5  # the public servers are usually at capacity; never let them hold up a plan
OVERPASS_COOLDOWN = 600  # after a failure, skip Overpass for 10 minutes
_overpass_down_until = 0.0
PHOTON_COOLDOWN = 300  # after Photon fails or is slow, skip live search for 5 minutes when curated data exists
_photon_down_until = 0.0
PHOTON_CACHE_S = 1800
_photon_cache: dict[tuple, tuple[float, list]] = {}

# category -> [(search text, osm_tag filter)] for Photon
PHOTON_QUERIES: dict[str, list[tuple[str, str]]] = {
    "park": [("park", "leisure:park")],
    "garden": [("garden", "leisure:garden")],
    "lake": [("lake", "natural:water")],
    "viewpoint": [("viewpoint", "tourism:viewpoint")],
    "museum": [("museum", "tourism:museum")],
    "gallery": [("gallery", "tourism:gallery"), ("art", "tourism:gallery")],
    "arts_centre": [("arts", "amenity:arts_centre"), ("centre", "amenity:arts_centre")],
    "live_music": [("music", "amenity:music_venue"), ("live music", "amenity:bar"), ("music", "amenity:pub")],
    "theatre": [("theatre", "amenity:theatre")],
    "cinema": [("cinema", "amenity:cinema")],
    "bookstore": [("books", "shop:books"), ("book", "shop:books")],
    "library": [("library", "amenity:library")],
    "market": [("market", "amenity:marketplace")],
    "mall": [("mall", "shop:mall")],
    "historic": [("monument", "historic"), ("fort", "historic"), ("palace", "historic")],
    "bowling": [("bowling", "leisure:bowling_alley")],
    "arcade": [("arcade", "leisure:amusement_arcade"), ("games", "leisure:amusement_arcade")],
    "escape_room": [("escape", "leisure:escape_game")],
    "sports": [("sports", "leisure:sports_centre")],
    "spa": [("spa", "leisure:spa")],
    "cafe": [("cafe", "amenity:cafe"), ("coffee", "amenity:cafe")],
    "restaurant": [("restaurant", "amenity:restaurant"), ("veg", "amenity:restaurant")],
    "fast_food": [("food", "amenity:fast_food")],
    "dessert": [("ice cream", "amenity:ice_cream"), ("bakery", "shop:bakery")],
    "street_food": [("food court", "amenity:food_court")],
    "bar": [("pub", "amenity:pub"), ("bar", "amenity:bar")],
}

VEG_NAME = re.compile(r"\b(pure veg|veg|vegetarian|vegan)\b", re.I)
VEG_LIKELY_NAME = re.compile(r"sagar|bhavan|bhawan|udupi|udipi|darshini|thali|annapoorn|sweets|upahar", re.I)
NONVEG_NAME = re.compile(r"non[- ]?veg|meat|chicken|mutton|biryani|kebab|fish|seafood|military|chettinad|barbeque|bbq|grill|steak", re.I)

# category -> Overpass selectors (each must also have a name)
SELECTORS: dict[str, list[str]] = {
    "park": ['["leisure"="park"]'],
    "garden": ['["leisure"="garden"]["access"!="private"]'],
    "lake": ['["natural"="water"]["water"~"^(lake|reservoir|pond)$"]'],
    "viewpoint": ['["tourism"="viewpoint"]'],
    "museum": ['["tourism"="museum"]'],
    "gallery": ['["tourism"="gallery"]'],
    "arts_centre": ['["amenity"="arts_centre"]'],
    "live_music": ['["amenity"="music_venue"]', '["live_music"="yes"]'],
    "theatre": ['["amenity"="theatre"]'],
    "cinema": ['["amenity"="cinema"]'],
    "bookstore": ['["shop"="books"]'],
    "library": ['["amenity"="library"]'],
    "market": ['["amenity"="marketplace"]'],
    "mall": ['["shop"="mall"]'],
    "historic": ['["historic"~"^(monument|memorial|castle|fort|palace|ruins|archaeological_site)$"]'],
    "bowling": ['["leisure"="bowling_alley"]'],
    "arcade": ['["leisure"="amusement_arcade"]'],
    "escape_room": ['["leisure"="escape_game"]'],
    "sports": ['["leisure"="sports_centre"]'],
    "spa": ['["leisure"="spa"]'],
    "cafe": ['["amenity"="cafe"]'],
    "restaurant": ['["amenity"="restaurant"]'],
    "fast_food": ['["amenity"="fast_food"]'],
    "dessert": ['["amenity"="ice_cream"]', '["shop"~"^(bakery|confectionery|pastry)$"]'],
    "street_food": ['["amenity"="food_court"]'],
    "bar": ['["amenity"~"^(bar|pub|biergarten)$"]'],
}

# Cuisines that are a poor bet for vegetarians even if the place is untagged.
MEAT_CUISINES = re.compile(r"steak|bbq|barbecue|kebab|chicken|seafood|fish|burger|meat|grill|biryani|mughlai|korean", re.I)


class PlacesUnavailable(Exception):
    pass


def classify(tags: dict) -> str | None:
    """Map OSM tags back to one of our categories."""
    for category, selectors in SELECTORS.items():
        for sel in selectors:
            ok = True
            for key, op, val in re.findall(r'\["([^"]+)"(=|!=|~)"([^"]+)"\]', sel):
                actual = tags.get(key)
                if op == "=" and actual != val:
                    ok = False
                elif op == "!=" and actual == val:
                    ok = False
                elif op == "~" and (actual is None or not re.search(val, actual)):
                    ok = False
            if ok:
                return category
    return None


async def _overpass(categories: list[str], lat: float, lon: float, radius_m: int) -> list[dict]:
    global _overpass_down_until
    if time.monotonic() < _overpass_down_until:
        raise PlacesUnavailable("Overpass skipped (failed recently).")
    parts = []
    for cat in categories:
        for sel in SELECTORS.get(cat, []):
            parts.append(f'nwr{sel}["name"](around:{radius_m},{lat:.5f},{lon:.5f});')
    query = f"[out:json][timeout:8];({''.join(parts)});out center tags 400;"
    try:
        async with httpx2.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
            resp = await client.post(OVERPASS_URL, data={"data": query}, timeout=OVERPASS_TIMEOUT)
            resp.raise_for_status()
            return resp.json().get("elements", [])
    except (httpx2.HTTPError, ValueError) as err:
        _overpass_down_until = time.monotonic() + OVERPASS_COOLDOWN
        raise PlacesUnavailable(f"Overpass unavailable ({type(err).__name__}).") from err


def _veg_from_name(name: str) -> tuple[str, str | None]:
    if NONVEG_NAME.search(name):
        return "no", None
    if VEG_NAME.search(name):
        return "yes", None
    if VEG_LIKELY_NAME.search(name):
        return "unverified", "name suggests a vegetarian eatery"
    return "unverified", None


def _veg_status(tags: dict) -> str:
    v = tags.get("diet:vegetarian") or tags.get("diet:vegan")
    if v in ("only", "yes", "no"):
        return v
    if re.search(r"vegetarian|vegan|jain", tags.get("cuisine", ""), re.I):
        return "yes"
    return _veg_from_name(tags.get("name", ""))[0]


async def _photon(categories: list[str], center: Location, radius_km: float, country_code: str, vegetarian: bool) -> list[Place]:
    global _photon_down_until
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(0.2, math.cos(math.radians(center.lat))))
    bbox = f"{center.lon - dlon:.5f},{center.lat - dlat:.5f},{center.lon + dlon:.5f},{center.lat + dlat:.5f}"

    async def one(client: httpx2.AsyncClient, category: str, text: str, tag: str) -> list[Place]:
        params = {"q": text, "lat": center.lat, "lon": center.lon, "limit": 15, "osm_tag": tag, "bbox": bbox, "lang": "en"}
        key = (text, tag, bbox)
        cached = _photon_cache.get(key)
        if cached and time.monotonic() - cached[0] < PHOTON_CACHE_S:
            features = cached[1]
        else:
            features = await photon_search(client, params)
            _photon_cache[key] = (time.monotonic(), features)
        out = []
        for f in features:
            props, coords = f.get("properties", {}), f.get("geometry", {}).get("coordinates")
            name = props.get("name")
            if not name or not coords:
                continue
            lon, lat = coords
            meta = category_meta(category, country_code)
            veg, note = _veg_from_name(name) if meta["kind"] == "food" else (None, None)
            address = ", ".join(x for x in (props.get("street"), props.get("locality") or props.get("district")) if x) or None
            out.append(Place(
                id=f"osm:{props.get('osm_type', 'x').lower()}{props.get('osm_id')}",
                name=name, category=category, kind=meta["kind"], lat=lat, lon=lon,
                distance_km=haversine_km(center.lat, center.lon, lat, lon),
                indoor=meta["indoor"], est_cost=meta["cost"], crowd=meta["crowd"],
                veg=veg, address=address, note=note,
            ))
        return out

    queries = [(cat, *PHOTON_QUERIES[cat][0]) for cat in categories if cat in PHOTON_QUERIES]
    if vegetarian and "restaurant" in categories:
        queries.append(("restaurant", "veg", "amenity:restaurant"))
    if "bar" in categories:
        queries.append(("bar", *PHOTON_QUERIES["bar"][1]))
    async with httpx2.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        results = await asyncio.gather(*(one(client, *q) for q in queries), return_exceptions=True)
    places = [p for r in results if isinstance(r, list) for p in r]
    if not places and all(isinstance(r, Exception) for r in results):
        _photon_down_until = time.monotonic() + PHOTON_COOLDOWN
        raise PlacesUnavailable("Photon is not responding.")
    return places


async def _live(categories: list[str], center: Location, radius_km: float, country_code: str, vegetarian: bool) -> tuple[list[Place], str]:
    """Query Overpass and Photon in parallel; prefer Overpass (richer tags) when it answers."""
    overpass = asyncio.create_task(_overpass(categories, center.lat, center.lon, int(radius_km * 1000)))
    photon = asyncio.create_task(_photon(categories, center, radius_km, country_code, vegetarian))
    try:
        elements = await overpass
        places = [p for el in elements if (p := _from_osm(el, center, country_code))]
        if places:
            photon.cancel()
            return places, "OpenStreetMap (Overpass)"
    except PlacesUnavailable:
        pass
    return await photon, "OpenStreetMap (Photon)"


async def _live_within(budget_s: float, *args) -> tuple[list[Place], str]:
    global _photon_down_until
    try:
        return await asyncio.wait_for(_live(*args), timeout=budget_s)
    except TimeoutError:
        _photon_down_until = time.monotonic() + PHOTON_COOLDOWN
        raise PlacesUnavailable(f"Live search took over {budget_s:g}s; using what's on hand.") from None


def _from_osm(el: dict, center: Location, country_code: str) -> Place | None:
    tags = el.get("tags") or {}
    category = classify(tags)
    if not category or not tags.get("name"):
        return None
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lon = el.get("lon") or (el.get("center") or {}).get("lon")
    if lat is None or lon is None:
        return None
    meta = category_meta(category, country_code)
    crowd = meta["crowd"]
    famous = bool(tags.get("wikidata") or tags.get("wikipedia") or tags.get("tourism") == "attraction")
    if famous and meta["kind"] == "activity" and crowd != "high":
        crowd = "high" if crowd == "medium" else "medium"
    fee = tags.get("fee")
    cost = 0 if fee == "no" else meta["cost"]
    return Place(
        id=f"osm:{el['type'][0]}{el['id']}",
        name=tags.get("name:en") or tags["name"],
        category=category,
        kind=meta["kind"],
        lat=lat,
        lon=lon,
        distance_km=haversine_km(center.lat, center.lon, lat, lon),
        indoor=meta["indoor"],
        est_cost=cost,
        crowd=crowd,
        opening_hours=tags.get("opening_hours"),
        cuisine=tags.get("cuisine"),
        veg=_veg_status(tags) if meta["kind"] == "food" else None,
        website=tags.get("website") or tags.get("contact:website"),
        note="well-known spot" if famous else None,
    )


def _score(p: Place, vegetarian: bool) -> float:
    score = -p.distance_km
    score += 0.6 * bool(p.opening_hours) + 0.4 * bool(p.website) + 0.3 * bool(p.cuisine)
    score += 2.0 * (p.source == "curated")  # well-known places with known hours and prices make plans feel real
    if vegetarian and p.veg:
        score += {"only": 3, "yes": 2, "unverified": 0, "no": -99}[p.veg]
    return score


def _diverse_top(places: list[Place], limit: int, vegetarian: bool) -> list[Place]:
    """Best places, round-robin across categories so one category can't crowd out the rest."""
    by_cat: dict[str, list[Place]] = {}
    for p in sorted(places, key=lambda x: _score(x, vegetarian), reverse=True):
        by_cat.setdefault(p.category, []).append(p)
    out: list[Place] = []
    while len(out) < limit and any(by_cat.values()):
        for cat in list(by_cat):
            if by_cat[cat] and len(out) < limit:
                out.append(by_cat[cat].pop(0))
    return out


JUNK_NAME = re.compile(r"(park|garden|playground|ground|lake|pond|cafe|restaurant|shop|store)\s*\d*", re.I)


def _filter(places: list[Place], indoor_only: bool, vegetarian: bool, max_price: float | None) -> list[Place]:
    out = []
    seen_names = set()
    for p in places:
        if p.name.lower() in seen_names or len(p.name) < 3 or JUNK_NAME.fullmatch(p.name.strip()):
            continue
        if indoor_only and not p.indoor:
            continue
        if vegetarian and p.kind == "food":
            if p.veg == "no" or (p.veg == "unverified" and p.cuisine and MEAT_CUISINES.search(p.cuisine)):
                continue
        # OSM has no prices: live places carry flat category estimates, so only filter places with known prices
        if max_price is not None and p.source == "curated" and p.est_cost > max_price * 1.25:
            continue
        seen_names.add(p.name.lower())
        out.append(p)
    return out


def curated_places(city: str, center: Location, kind: str, categories: list[str], strict: bool = False) -> list[Place]:
    rows = curated_for(city) or []
    places = []
    for name, category, lat, lon, extra in rows:
        meta = category_meta(category, "IN")
        if meta["kind"] != kind:
            continue
        places.append(Place(
            id="cur:" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
            name=name,
            category=category,
            kind=meta["kind"],
            lat=lat,
            lon=lon,
            distance_km=haversine_km(center.lat, center.lon, lat, lon),
            indoor=meta["indoor"],
            est_cost=extra.get("est_cost", meta["cost"]),
            crowd=extra.get("crowd", meta["crowd"]),
            opening_hours=extra.get("opening_hours"),
            cuisine=extra.get("cuisine"),
            veg=extra.get("veg") if kind == "food" else None,
            source="curated",
            note=extra.get("note"),
        ))
    wanted = [p for p in places if p.category in categories]
    return wanted if (wanted or strict) else places


def generic_places(center: Location, kind: str, country_code: str) -> list[Place]:
    """Last resort when there is no live or curated data: honest, generic suggestions."""
    if kind == "activity":
        specs = [("A walk around the city centre", "park"), ("The main public library or bookshop near the centre", "library")]
    else:
        specs = [("A well-reviewed café near the centre (check a maps app)", "cafe"), ("A local vegetarian-friendly restaurant near the centre", "restaurant")]
    out = []
    for i, (name, category) in enumerate(specs):
        meta = category_meta(category, country_code)
        out.append(Place(
            id=f"gen:{kind}-{i}", name=name, category=category, kind=meta["kind"],
            lat=center.lat, lon=center.lon, indoor=meta["indoor"], est_cost=meta["cost"],
            crowd=meta["crowd"], source="curated", veg="unverified" if kind == "food" else None,
            note="generic suggestion; live place data unavailable",
        ))
    return out


async def find_places(
    *,
    kind: str,
    categories: list[str],
    center: Location,
    city: str,
    country_code: str,
    radius_km: float,
    vegetarian: bool = False,
    indoor_only: bool = False,
    max_price: float | None = None,
    limit: int = 12,
) -> tuple[list[Place], str, list[str]]:
    """Returns (places, source, notes). source is 'live' (some live results), 'curated' or 'generic'."""
    notes: list[str] = []
    categories = [c for c in categories if c in SELECTORS] or (["park", "museum", "bookstore"] if kind == "activity" else ["cafe", "restaurant"])
    radius_km = max(0.5, min(radius_km, 12))

    # Curated first: well-known places with hours, prices and veg status, near the search point.
    curated = _filter(curated_places(city, center, kind, categories, strict=True), indoor_only, vegetarian, max_price)
    curated = [p for p in curated if p.distance_km <= max(radius_km * 1.6, 6)]  # stay in the user's part of town

    live: list[Place] = []
    live_budget = 3.0 if len(curated) >= 5 else 8.0  # curated data already covers this area: don't wait long for live
    try:
        if time.monotonic() < _photon_down_until and curated:
            raise PlacesUnavailable("Live map search is struggling right now; using curated places.")
        raw, provider = await _live_within(live_budget, categories, center, radius_km, country_code, vegetarian)
        live = _filter(raw, indoor_only, vegetarian, max_price)
        if not live and not curated and radius_km < 12:
            wider = min(12, radius_km * 2)
            notes.append(f"No matches within {radius_km:g} km, widened the search to {wider:g} km.")
            raw, provider = await _live_within(live_budget, categories, center, wider, country_code, vegetarian)
            live = _filter(raw, indoor_only, vegetarian, max_price)
        if live:
            notes.append(f"Live data from {provider}.")
    except PlacesUnavailable as err:
        notes.append(str(err))

    curated_names = {_norm(p.name) for p in curated}
    live = [p for p in live if _norm(p.name) not in curated_names and not any(_norm(p.name) in c or c in _norm(p.name) for c in curated_names)]
    merged = curated + live
    if not merged:  # nothing in the categories asked for: any curated place of this kind, then generic
        merged = sorted(_filter(curated_places(city, center, kind, categories), indoor_only, vegetarian, max_price), key=lambda p: p.distance_km)[:6]
        if merged:
            notes.append(f"No matches for these categories; showing other curated options (nearest {merged[0].distance_km:.0f} km).")
    if not merged:
        notes.append("No live or curated data here; returning generic suggestions.")
        return generic_places(center, kind, country_code), "generic", notes

    picked = _diverse_top(merged, limit, vegetarian)
    n_cur = sum(p.source == "curated" for p in picked)
    notes.insert(0, f"{n_cur} curated, {len(picked) - n_cur} live OpenStreetMap")
    return picked, ("live" if n_cur < len(picked) else "curated"), notes


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
