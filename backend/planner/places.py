"""Real places from live map APIs, with curated and generic fallbacks.

Live sources:
  * Geoapify Places (when GEOAPIFY_API_KEY is set): fast, reliable category search over OpenStreetMap,
    with OSM tags (opening hours, diet, cuisine) attached.
  * Photon (komoot), keyless: OSM search with category filters, but names and locations only. Used
    without a Geoapify key or when Geoapify fails.
Curated data only fills gaps when live results are thin; generic suggestions are the last resort.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import time

import httpx2

from .fallback_data import curated_for
from .geo import USER_AGENT, haversine_km
from .models import Location, Place
from .pricing import category_meta, place_cost

PHOTON_URL = "https://photon.komoot.io/api/"
_photon_slots = asyncio.Semaphore(3)
_photon_down_until = 0.0
PHOTON_COOLDOWN = 300
_photon_cache: dict[tuple, tuple[float, list]] = {}
PHOTON_CACHE_S = 1800
GEOAPIFY_URL = "https://api.geoapify.com/v2/places"
GEOAPIFY_SPACING = 0.21  # free tier allows 5 requests a second
GEOAPIFY_COOLDOWN = 300
GEOAPIFY_TIMEOUT = 3.5  # sparse categories can take seconds; a slow one is skipped rather than holding up the rest
NEAREST_KM = 25  # how far to look for something the user explicitly asked for when there is none nearby
_geoapify_lock = asyncio.Lock()
_geoapify_last = 0.0
_geoapify_down_until = 0.0
_geoapify_cache: dict[tuple, tuple[float, list]] = {}

# category -> Geoapify category keys (https://apidocs.geoapify.com/docs/places/#categories)
GEOAPIFY_CATEGORIES: dict[str, str] = {
    "park": "leisure.park",
    "garden": "leisure.park.garden",
    "lake": "natural.water",
    "viewpoint": "tourism.attraction.viewpoint",
    "beach": "beach",
    "museum": "entertainment.museum",
    "gallery": "entertainment.culture.gallery",
    "arts_centre": "entertainment.culture.arts_centre",
    "live_music": "entertainment.culture.arts_centre,entertainment.culture.theatre",  # no music-venue category
    "theatre": "entertainment.culture.theatre",
    "cinema": "entertainment.cinema",
    "bookstore": "commercial.books",
    "library": "education.library",
    "market": "commercial.marketplace",
    "mall": "commercial.shopping_mall",
    "historic": "tourism.sights.fort,tourism.sights.castle,tourism.sights.memorial,tourism.sights.archaeological_site,tourism.sights.ruines,heritage.unesco",
    "bowling": "entertainment.bowling_alley",
    "arcade": "entertainment.amusement_arcade",  # entertainment.activity_park takes 6-12 s to answer
    "escape_room": "entertainment.escape_game",
    "sports": "sport.sports_centre",
    "spa": "leisure.spa",
    "cafe": "catering.cafe",
    "restaurant": "catering.restaurant",
    "fast_food": "catering.fast_food",
    "dessert": "catering.ice_cream,catering.cafe.dessert,catering.cafe.ice_cream,catering.cafe.cake,commercial.food_and_drink.bakery,commercial.food_and_drink.confectionery",
    "street_food": "catering.food_court",
    "bar": "catering.bar,catering.pub,catering.biergarten,catering.taproom",
}

# category -> (search text, osm_tag filter) for Photon
PHOTON_QUERIES: dict[str, tuple[str, str]] = {
    "park": ("park", "leisure:park"),
    "garden": ("garden", "leisure:garden"),
    "lake": ("lake", "natural:water"),
    "viewpoint": ("viewpoint", "tourism:viewpoint"),
    "beach": ("beach", "natural:beach"),
    "museum": ("museum", "tourism:museum"),
    "gallery": ("gallery", "tourism:gallery"),
    "arts_centre": ("arts", "amenity:arts_centre"),
    "live_music": ("music", "amenity:music_venue"),
    "theatre": ("theatre", "amenity:theatre"),
    "cinema": ("cinema", "amenity:cinema"),
    "bookstore": ("books", "shop:books"),
    "library": ("library", "amenity:library"),
    "market": ("market", "amenity:marketplace"),
    "mall": ("mall", "shop:mall"),
    "historic": ("monument", "historic"),
    "bowling": ("bowling", "leisure:bowling_alley"),
    "arcade": ("arcade", "leisure:amusement_arcade"),
    "escape_room": ("escape", "leisure:escape_game"),
    "sports": ("sports", "leisure:sports_centre"),
    "spa": ("spa", "leisure:spa"),
    "cafe": ("cafe", "amenity:cafe"),
    "restaurant": ("restaurant", "amenity:restaurant"),
    "fast_food": ("food", "amenity:fast_food"),
    "dessert": ("ice cream", "amenity:ice_cream"),
    "street_food": ("food court", "amenity:food_court"),
    "bar": ("pub", "amenity:pub"),
}

# cuisine wish -> (words in the user's request, Geoapify categories, pattern for a place's cuisine tag or name)
CUISINES: dict[str, tuple[str, str, str]] = {
    "north indian": (r"north indian|punjabi|mughlai|tandoor|butter chicken|dal makhani|dhaba", "catering.restaurant.indian",
                     r"north_indian|punjabi|mughlai|tandoor|dhaba|\bindian"),
    "south indian": (r"south indian|dosa|idli|udupi", "catering.restaurant.indian", r"south_indian|udupi|udipi|dosa|idli|darshini"),
    "biryani": (r"biryani", "catering.restaurant.indian", r"biryani|hyderabadi|mughlai"),
    "chinese": (r"chinese|\bmomos?\b|dim sum", "catering.restaurant.chinese,catering.restaurant.dumpling", r"chinese|momo|dumpling"),
    "italian": (r"italian|pasta|pizza", "catering.restaurant.italian,catering.restaurant.pizza", r"italian|pizza|pasta"),
    "japanese": (r"japanese|sushi|ramen", "catering.restaurant.japanese,catering.restaurant.sushi,catering.restaurant.ramen", r"japanese|sushi|ramen"),
    "asian": (r"pan.?asian|\bthai\b|korean|burmese|vietnamese", "catering.restaurant.asian,catering.restaurant.thai,catering.restaurant.korean",
              r"asian|thai|korean|burmese|vietnamese"),
    "seafood": (r"seafood|\bfish\b|malvani|coastal food", "catering.restaurant.seafood,catering.restaurant.fish", r"seafood|fish|malvani|coastal"),
}


def cuisine_wish(text: str) -> str | None:
    """The first cuisine the user's words ask for, if any ("a good north indian lunch" -> "north indian")."""
    return next((k for k, (words, _, _) in CUISINES.items() if re.search(words, text, re.I)), None)


VEG_NAME = re.compile(r"\b(pure veg|veg|vegetarian|vegan)\b", re.I)
VEG_LIKELY_NAME = re.compile(r"sagar|bhavan|bhawan|udupi|udipi|darshini|thali|annapoorn|sweets|upahar", re.I)
NONVEG_NAME = re.compile(r"non[- ]?veg|meat|chicken|mutton|biryani|kebab|fish|seafood|military|chettinad|barbeque|bbq|grill|steak", re.I)

# Cuisines that are a poor bet for vegetarians even if the place is untagged.
MEAT_CUISINES = re.compile(r"steak|bbq|barbecue|kebab|chicken|seafood|fish|burger|meat|grill|biryani|mughlai|korean", re.I)


class PlacesUnavailable(Exception):
    pass


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
            for attempt in range(2):
                async with _photon_slots:  # the public server 503s on bursts
                    resp = await client.get(PHOTON_URL, params=params, timeout=5.0)
                if resp.status_code not in (429, 503) or attempt:
                    break
                await asyncio.sleep(0.6)
            resp.raise_for_status()
            features = resp.json().get("features", [])
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
                indoor=meta["indoor"], est_cost=place_cost(name, category, country_code), crowd=meta["crowd"],
                veg=veg, address=address, note=note,
            ))
        return out

    queries = [(cat, *PHOTON_QUERIES[cat]) for cat in categories if cat in PHOTON_QUERIES]
    if vegetarian and "restaurant" in categories:
        queries.append(("restaurant", "veg", "amenity:restaurant"))
    if "bar" in categories:
        queries.append(("bar", "bar", "amenity:bar"))  # PHOTON_QUERIES["bar"] covers pubs
    async with httpx2.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        results = await asyncio.gather(*(one(client, *q) for q in queries), return_exceptions=True)
    places = [p for r in results if isinstance(r, list) for p in r]
    if not places and all(isinstance(r, Exception) for r in results):
        raise PlacesUnavailable("Photon is not responding.")
    return places

def geoapify_key() -> str | None:
    return os.environ.get("GEOAPIFY_API_KEY") or None


async def _geoapify_slot() -> None:
    """Space out request starts to stay under the free tier's rate limit."""
    global _geoapify_last
    async with _geoapify_lock:
        wait = _geoapify_last + GEOAPIFY_SPACING - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _geoapify_last = time.monotonic()


async def _geoapify(categories: list[str], center: Location, radius_km: float, country_code: str, vegetarian: bool, limit: int = 20,
                    cuisine: str | None = None) -> list[Place]:
    global _geoapify_down_until
    key = geoapify_key()
    if not key or time.monotonic() < _geoapify_down_until:
        raise PlacesUnavailable("Geoapify not configured.")
    radius_m = int(radius_km * 1000)

    async def one(client: httpx2.AsyncClient, category: str, conditions: str | None, keys: str | None = None) -> list[Place]:
        params = {
            "categories": keys or GEOAPIFY_CATEGORIES[category],
            "filter": f"circle:{center.lon:.5f},{center.lat:.5f},{radius_m}",
            "bias": f"proximity:{center.lon:.5f},{center.lat:.5f}",
            "limit": limit,  # 20 places = 1 credit
            "lang": "en",
        }
        if conditions:
            params["conditions"] = conditions
        cache_key = (params["categories"], params["filter"], conditions, limit)
        cached = _geoapify_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < PHOTON_CACHE_S:
            features = cached[1]
        else:
            for attempt in range(2):
                await _geoapify_slot()
                resp = await client.get(GEOAPIFY_URL, params={**params, "apiKey": key}, timeout=GEOAPIFY_TIMEOUT)
                if resp.status_code != 429 or attempt:
                    break
                await asyncio.sleep(1.0)
            if resp.status_code in (401, 403):
                raise PlacesUnavailable(f"Geoapify rejected the API key ({resp.status_code}).")
            resp.raise_for_status()
            features = resp.json().get("features", [])
            _geoapify_cache[cache_key] = (time.monotonic(), features)
        return [p for f in features if (p := _from_geoapify(f, category, center, country_code))]

    requests = [(cat, None) for cat in categories if cat in GEOAPIFY_CATEGORIES]
    if vegetarian and "restaurant" in categories:  # few places are diet-tagged, so this tops up rather than filters
        requests.append(("restaurant", "vegetarian"))
    if cuisine in CUISINES and "restaurant" in categories:  # the nearest 20 restaurants may not include the cuisine asked for
        requests.append(("restaurant", None, CUISINES[cuisine][1]))
    async with httpx2.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        results = await asyncio.gather(*(one(client, *r) for r in requests), return_exceptions=True)
    places = [p for r in results if isinstance(r, list) for p in r]
    errors = [r for r in results if isinstance(r, Exception)]
    if errors and not places:
        if any("rejected the API key" in str(e) for e in errors):
            _geoapify_down_until = time.monotonic() + GEOAPIFY_COOLDOWN
        raise PlacesUnavailable(f"Geoapify unavailable ({_safe_error(errors[0])}).")
    return places


def _safe_error(err: Exception) -> str:
    """Describe a request failure without its URL, which carries the API key."""
    if isinstance(err, httpx2.HTTPStatusError):
        return f"HTTP {err.response.status_code}"
    return type(err).__name__ if not isinstance(err, PlacesUnavailable) else str(err)


def _from_geoapify(f: dict, requested: str, center: Location, country_code: str) -> Place | None:
    props = f.get("properties") or {}
    raw = (props.get("datasource") or {}).get("raw") or {}
    name = raw.get("name:en") or props.get("name")
    lat, lon = props.get("lat"), props.get("lon")
    if not name or lat is None or lon is None:
        return None
    cats = props.get("categories") or []
    category = requested
    if requested == "live_music":  # the search returns theatres and arts centres: label them as what they are
        category = "theatre" if "entertainment.culture.theatre" in cats else "arts_centre"
    meta = category_meta(category, country_code)
    famous = bool(raw.get("wikidata") or raw.get("wikipedia") or "tourism.attraction" in cats or "heritage" in cats)
    crowd = meta["crowd"]
    if famous and meta["kind"] == "activity" and crowd != "high":
        crowd = "high" if crowd == "medium" else "medium"
    veg = None
    if meta["kind"] == "food":
        if "vegetarian.only" in cats or "vegan.only" in cats:
            veg = "only"
        elif "vegetarian" in cats or "vegan" in cats:
            veg = "yes"
        else:
            veg = _veg_status({**raw, "name": name})
    catering = props.get("catering") or {}
    osm = f"osm:{raw['osm_type']}{raw['osm_id']}" if raw.get("osm_type") and raw.get("osm_id") else None
    return Place(
        id=osm or f"geo:{str(props.get('place_id', name))[:24]}",
        name=name,
        category=category,
        kind=meta["kind"],
        lat=lat,
        lon=lon,
        distance_km=haversine_km(center.lat, center.lon, lat, lon),
        indoor=meta["indoor"],
        est_cost=0 if raw.get("fee") == "no" else place_cost(name, category, country_code),
        crowd=crowd,
        opening_hours=props.get("opening_hours") or raw.get("opening_hours"),
        cuisine=catering.get("cuisine") or raw.get("cuisine"),
        veg=veg,
        website=props.get("website") or raw.get("website") or raw.get("contact:website"),
        address=props.get("address_line2") or None,
        note="well-known spot" if famous else None,
    )


async def _live(categories: list[str], center: Location, radius_km: float, country_code: str, vegetarian: bool,
                cuisine: str | None = None) -> tuple[list[Place], str]:
    """Geoapify when configured; otherwise (or if it fails) Photon."""
    fell_back = ""
    if geoapify_key():
        try:
            return await _geoapify(categories, center, radius_km, country_code, vegetarian, cuisine=cuisine), "Geoapify Places (OpenStreetMap)"
        except PlacesUnavailable as err:
            fell_back = f" ({err})"
    return await _photon(categories, center, radius_km, country_code, vegetarian), "OpenStreetMap (Photon)" + fell_back


def _score(p: Place, vegetarian: bool, cuisine: str | None = None) -> float:
    score = -p.distance_km
    score += 0.6 * bool(p.opening_hours) + 0.4 * bool(p.website) + 0.3 * bool(p.cuisine)
    if vegetarian and p.veg:
        score += {"only": 3, "yes": 2, "unverified": 0, "no": -99}[p.veg]
    if cuisine in CUISINES and p.kind == "food" and re.search(CUISINES[cuisine][2], f"{p.cuisine or ''} {p.name}", re.I):
        score += 3
    return score


def _diverse_top(places: list[Place], limit: int, vegetarian: bool, cuisine: str | None = None) -> list[Place]:
    """Best places, round-robin across categories so one category can't crowd out the rest."""
    by_cat: dict[str, list[Place]] = {}
    for p in sorted(places, key=lambda x: _score(x, vegetarian, cuisine), reverse=True):
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
    must_have: list[str] | None = None,
    cuisine: str | None = None,
) -> tuple[list[Place], str, list[str]]:
    """Returns (places, source, notes). source is 'live' (some live results), 'curated' or 'generic'.

    Live API results come first. Curated places enrich live matches (known hours, prices, veg status)
    and fill gaps: categories the live search missed, or areas where it found too little. For must_have
    categories (ones the user asked for by name) with nothing nearby, the nearest match within 25 km is added.
    """
    notes: list[str] = []
    categories = [c for c in categories if c in GEOAPIFY_CATEGORIES] or (["park", "museum", "bookstore"] if kind == "activity" else ["cafe", "restaurant"])
    radius_km = max(0.5, min(radius_km, 12))

    curated = _filter(curated_places(city, center, kind, categories, strict=True), indoor_only, vegetarian, max_price)
    curated = [p for p in curated if p.distance_km <= max(radius_km * 1.6, 6)]  # stay in the user's part of town

    global _photon_down_until
    live: list[Place] = []
    has_geoapify = bool(geoapify_key()) and time.monotonic() >= _geoapify_down_until
    # Geoapify answers in about a second; the public OSM servers are slow, so don't wait on them when curated data covers the area.
    live_budget = 6.0 if has_geoapify else (3.0 if len(curated) >= 5 else 8.0)
    try:
        if not has_geoapify and time.monotonic() < _photon_down_until and curated:
            raise PlacesUnavailable("Live map search is struggling right now; using curated places.")
        try:
            raw, provider = await asyncio.wait_for(_live(categories, center, radius_km, country_code, vegetarian, cuisine), timeout=live_budget)
        except asyncio.TimeoutError:
            if not has_geoapify:
                _photon_down_until = time.monotonic() + PHOTON_COOLDOWN
            raise PlacesUnavailable(f"Live search took over {live_budget:g}s; using what's on hand.")
        live = _filter(raw, indoor_only, vegetarian, max_price)
        if len(live) < 4 and radius_km < 12 and (has_geoapify or not curated):
            wider = min(12, radius_km * 2)
            try:
                raw, provider = await asyncio.wait_for(_live(categories, center, wider, country_code, vegetarian, cuisine), timeout=live_budget)
                wider_live = _filter(raw, indoor_only, vegetarian, max_price)
                if len(wider_live) > len(live):
                    notes.append(f"Only {len(live)} live matches within {radius_km:g} km, widened the search to {wider:g} km.")
                    live = wider_live
            except (asyncio.TimeoutError, PlacesUnavailable):
                pass
        if live:
            notes.append(f"Live data from {provider}.")
        # live_music has no Geoapify category of its own (its search returns theatres and arts centres), so there is no "nearest" to find.
        missing = [c for c in (must_have or []) if c in categories and c != "live_music" and not any(p.category == c for p in live)][:2]
        if missing and has_geoapify:
            live += await _nearest(missing, center, radius_km, country_code, vegetarian, indoor_only, max_price, notes)
    except PlacesUnavailable as err:
        if "Photon" in str(err):
            _photon_down_until = time.monotonic() + PHOTON_COOLDOWN
        notes.append(str(err))

    seen_ids: set[str] = set()
    live = [p for p in live if not (p.id in seen_ids or seen_ids.add(p.id))]  # the same place can come back from two searches
    live, leftovers = _enrich(live, curated)
    merged = list(live)
    per_cat: dict[str, int] = {}
    for p in merged:
        per_cat[p.category] = per_cat.get(p.category, 0) + 1
    filled = 0
    for c in sorted(leftovers, key=lambda p: p.distance_km):  # curated fills categories live missed, and thin areas
        if per_cat.get(c.category, 0) < 2 or len(merged) < 6:
            merged.append(c)
            per_cat[c.category] = per_cat.get(c.category, 0) + 1
            filled += 1
    if live and filled:
        notes.append(f"Live results were thin for some categories; added {filled} curated places.")
    if not merged:  # nothing in the categories asked for: any curated place of this kind, then generic
        merged = sorted(_filter(curated_places(city, center, kind, categories), indoor_only, vegetarian, max_price), key=lambda p: p.distance_km)[:6]
        if merged:
            notes.append(f"No matches for these categories; showing other curated options (nearest {merged[0].distance_km:.0f} km).")
    if not merged:
        notes.append("No live or curated data here; returning generic suggestions.")
        return generic_places(center, kind, country_code), "generic", notes

    if len(merged) < 6:  # thin results: offer nearby well-known places from related categories too
        have = {_norm(p.name) for p in merged}
        extra = [p for p in _filter(curated_places(city, center, kind, []), indoor_only, vegetarian, max_price)
                 if _norm(p.name) not in have and p.distance_km <= max(radius_km * 1.6, 6)]
        extra.sort(key=lambda p: p.distance_km)
        if extra[: 6 - len(merged)]:
            notes.append(f"Added {len(extra[: 6 - len(merged)])} nearby curated places from related categories.")
        merged += extra[: 6 - len(merged)]

    picked = _diverse_top(merged, limit, vegetarian, cuisine)
    if cuisine in CUISINES and kind == "food":
        n = sum(bool(re.search(CUISINES[cuisine][2], f"{p.cuisine or ''} {p.name}", re.I)) for p in picked)
        notes.append(f"Favoured {cuisine} places ({n} in the list)." if n else f"No {cuisine} places found nearby.")
    n_cur = sum(p.source == "curated" for p in picked)
    notes.insert(0, f"{len(picked) - n_cur} live, {n_cur} curated")
    return picked, ("live" if n_cur < len(picked) else "curated"), notes


async def _nearest(categories: list[str], center: Location, radius_km: float, country_code: str, vegetarian: bool,
                   indoor_only: bool, max_price: float | None, notes: list[str]) -> list[Place]:
    """The closest few places for categories the user asked for by name but that have nothing within the radius."""
    try:
        found = _filter(await _geoapify(categories, center, NEAREST_KM, country_code, vegetarian, limit=3), indoor_only, vegetarian, max_price)
    except PlacesUnavailable:
        return []
    for c in categories:
        hits = sorted((p for p in found if p.category == c), key=lambda p: p.distance_km)
        label = c.replace("_", " ")
        if hits:
            notes.append(f"No {label} within {radius_km:g} km; the nearest in the map data is {hits[0].name}, {hits[0].distance_km:.0f} km away.")
        else:
            notes.append(f"No {label} within {NEAREST_KM} km in the map data.")
    return found


def _enrich(live: list[Place], curated: list[Place]) -> tuple[list[Place], list[Place]]:
    """Copy what curated data knows (prices, hours, veg status) onto the live place it matches.

    Returns (live places, curated places that matched nothing).
    """
    unmatched = list(curated)
    out = []
    for p in live:
        n = _norm(p.name)
        match = next((c for c in unmatched if haversine_km(p.lat, p.lon, c.lat, c.lon) < 1.0
                      and (_norm(c.name) == n or (min(len(n), len(_norm(c.name))) >= 5 and (n in _norm(c.name) or _norm(c.name) in n)))), None)
        if match:
            unmatched.remove(match)
            p = p.model_copy(update={
                "est_cost": match.est_cost,
                "crowd": match.crowd,
                "opening_hours": p.opening_hours or match.opening_hours,
                "cuisine": p.cuisine or match.cuisine,
                "veg": match.veg if p.veg in (None, "unverified") else p.veg,
                "note": match.note or p.note,
            })
        out.append(p)
    return out, unmatched


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
