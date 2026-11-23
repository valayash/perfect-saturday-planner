"""Category metadata and price heuristics.

OpenStreetMap has no prices or crowd levels, so these are rough estimates. Tuned
for India (INR), with a generic table for everywhere else. The UI labels all of it "est.".
"""

from __future__ import annotations

# category -> (kind, indoor, crowd on a Saturday, est. cost per person INR, est. cost elsewhere)
CATEGORIES: dict[str, tuple[str, bool, str, float, float]] = {
    # activities
    "park": ("activity", False, "medium", 0, 0),
    "garden": ("activity", False, "low", 0, 0),
    "lake": ("activity", False, "medium", 0, 0),
    "viewpoint": ("activity", False, "medium", 0, 0),
    "museum": ("activity", True, "medium", 150, 15),
    "gallery": ("activity", True, "low", 0, 0),
    "arts_centre": ("activity", True, "low", 200, 10),
    "live_music": ("activity", True, "medium", 600, 20),
    "theatre": ("activity", True, "medium", 500, 35),
    "cinema": ("activity", True, "medium", 350, 14),
    "bookstore": ("activity", True, "low", 0, 0),
    "library": ("activity", True, "low", 0, 0),
    "market": ("activity", False, "high", 0, 0),
    "mall": ("activity", True, "high", 0, 0),
    "historic": ("activity", False, "medium", 50, 8),
    "bowling": ("activity", True, "medium", 450, 15),
    "arcade": ("activity", True, "medium", 500, 15),
    "escape_room": ("activity", True, "low", 900, 30),
    "sports": ("activity", True, "low", 300, 15),
    "spa": ("activity", True, "low", 1200, 50),
    # food
    "cafe": ("food", True, "medium", 350, 10),
    "restaurant": ("food", True, "medium", 550, 25),
    "fast_food": ("food", True, "medium", 250, 12),
    "dessert": ("food", True, "low", 180, 7),
    "street_food": ("food", False, "high", 150, 8),
    "bar": ("food", True, "medium", 800, 20),
}

ACTIVITY_CATEGORIES = [c for c, meta in CATEGORIES.items() if meta[0] == "activity"]
FOOD_CATEGORIES = [c for c, meta in CATEGORIES.items() if meta[0] == "food"]


def is_india(country_code: str | None) -> bool:
    return (country_code or "").upper() == "IN"


def category_meta(category: str, country_code: str | None) -> dict:
    kind, indoor, crowd, inr, other = CATEGORIES.get(category, ("activity", False, "medium", 0, 0))
    return {"kind": kind, "indoor": indoor, "crowd": crowd, "cost": inr if is_india(country_code) else other}


def travel_rates(country_code: str | None) -> dict:
    if is_india(country_code):
        return {"mode": "auto/cab", "base": 40, "per_km": 18, "kmh": 18, "pickup_min": 6}
    return {"mode": "transit/taxi", "base": 3, "per_km": 1.8, "kmh": 22, "pickup_min": 5}
