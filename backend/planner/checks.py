"""Deterministic cost estimation and plan validation (no LLM involved)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .geo import travel_leg
from .hours import open_during
from .models import Location, Place, Preferences


@dataclass
class RunContext:
    prefs: Preferences
    city: Location
    center: Location
    places: dict[str, Place] = field(default_factory=dict)
    weather: dict | None = None

    @property
    def vegetarian(self) -> bool:
        return any(re.search(r"veg|vegan|jain", c, re.I) for c in self.prefs.hard_constraints)

    @property
    def avoid_crowds(self) -> bool:
        cons = self.prefs.hard_constraints + self.prefs.soft_constraints
        return any(re.search(r"crowd|quiet|busy|calm", c, re.I) for c in cons)


class StopIn(BaseModel):
    place_id: str
    start_time: str = Field(pattern=r"^\d{1,2}:\d{2}$")
    duration_minutes: int = Field(ge=10, le=360)
    spend_estimate: float | None = Field(default=None, ge=0)


def to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def to_hhmm(minutes: int) -> str:
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def itinerary(ctx: RunContext, stops: list[StopIn]) -> tuple[list[dict], list[str]]:
    """Resolve stops against known places and compute travel legs. Returns (rows, unknown_ids)."""
    rows, unknown = [], []
    prev: Place | None = None
    for s in sorted(stops, key=lambda s: to_min(s.start_time)):
        place = ctx.places.get(s.place_id)
        if not place:
            unknown.append(s.place_id)
            continue
        leg = travel_leg(prev.lat, prev.lon, place.lat, place.lon, ctx.city.country_code) if prev else None
        start = to_min(s.start_time)
        rows.append({
            "stop": s,
            "place": place,
            "start": start,
            "end": start + s.duration_minutes,
            "cost": s.spend_estimate if s.spend_estimate is not None else place.est_cost,
            "leg": leg,
        })
        prev = place
    return rows, unknown


def estimate_cost(ctx: RunContext, stops: list[StopIn]) -> dict:
    rows, unknown = itinerary(ctx, stops)
    stop_costs = [{"name": r["place"].name, "cost": round(r["cost"])} for r in rows]
    legs = [
        {"to": r["place"].name, **r["leg"]} for r in rows if r["leg"]
    ]
    total = sum(c["cost"] for c in stop_costs) + sum(l["cost"] for l in legs)
    budget = ctx.prefs.budget
    return {
        "currency": ctx.prefs.currency,
        "stops": stop_costs,
        "travel": legs,
        "total": round(total),
        "budget": round(budget),
        "remaining": round(budget - total),
        "unknown_place_ids": unknown,
        "note": "Estimates from category price heuristics, not live prices. Travel from home to the first stop isn't included.",
    }


def _rain_at(ctx: RunContext, minute: int) -> int:
    if not ctx.weather:
        return 0
    hour = f"{(minute // 60) % 24:02d}:00"
    for h in ctx.weather.get("hourly", []):
        if h["hour"] == hour:
            return h["rain_chance"]
    return 0


def validate_plan(ctx: RunContext, stops: list[StopIn]) -> dict:
    p = ctx.prefs
    errors: list[str] = []
    warnings: list[str] = []
    rows, unknown = itinerary(ctx, stops)

    for pid in unknown:
        errors.append(f"place_id '{pid}' was not returned by any search tool. Only use real places from tool results.")
    if not rows:
        errors.append("The plan has no valid stops.")
        return {"ok": False, "errors": errors, "warnings": warnings}

    window_start = to_min(p.start_time)
    window_end = window_start + p.available_minutes
    plan_start, plan_end = rows[0]["start"], rows[-1]["end"]

    if plan_start < window_start - 15:
        errors.append(f"Plan starts at {to_hhmm(plan_start)} but the user is only free from {p.start_time}.")
    if plan_end > window_end + 15:
        errors.append(f"Plan ends at {to_hhmm(plan_end)}, past the {to_hhmm(window_end)} cutoff ({p.available_minutes} min available).")
    used = plan_end - max(plan_start, window_start)
    if used < p.available_minutes * 0.5:
        warnings.append(f"Plan only uses {used} of {p.available_minutes} available minutes. Fine if intentional (e.g. low energy); say so.")

    travel_min = 0
    for prev, cur in zip(rows, rows[1:]):
        leg = cur["leg"]
        travel_min += leg["minutes"]
        gap = cur["start"] - prev["end"]
        if gap < leg["minutes"] - 10:
            errors.append(
                f"Not enough time to get from {prev['place'].name} to {cur['place'].name}: "
                f"{gap} min gap but ~{leg['minutes']} min by {leg['mode']} ({leg['km']} km)."
            )
        if leg["km"] > 15:
            errors.append(f"{prev['place'].name} → {cur['place'].name} is ~{leg['km']} km. Too far for one Saturday outing; keep stops clustered.")
        elif leg["km"] > 8:
            warnings.append(f"{prev['place'].name} → {cur['place'].name} is a long hop (~{leg['km']} km, {leg['minutes']} min).")
    if travel_min > 0.3 * max(used, 1):
        warnings.append(f"{travel_min} min of the plan is travel. Consider fewer or closer stops.")

    est = estimate_cost(ctx, stops)
    total, budget = est["total"], p.budget
    if total > budget * 1.15:
        errors.append(f"Estimated total {total} {p.currency} is well over the {round(budget)} budget.")
    elif total > budget:
        warnings.append(f"Estimated total {total} {p.currency} is slightly over the {round(budget)} budget. Explain the trade-off or trim.")

    sunset = (ctx.weather or {}).get("sunset")
    unknown_hours = []
    for r in rows:
        place, start, end = r["place"], r["start"], r["end"]
        is_open = open_during(place.opening_hours, start, end)
        if is_open is False:
            errors.append(f"{place.name} looks closed for {to_hhmm(start)}–{to_hhmm(end)} on Saturday (OSM hours: {place.opening_hours}).")
        elif is_open is None and place.source == "openstreetmap":
            unknown_hours.append(place.name)
        if ctx.vegetarian and place.kind == "food":
            if place.veg == "no":
                errors.append(f"{place.name} is tagged as not vegetarian, which breaks a hard constraint.")
            elif place.veg == "unverified":
                warnings.append(f"{place.name} isn't tagged vegetarian in OSM. Tell the user to confirm veg options.")
        if ctx.avoid_crowds and place.crowd == "high":
            warnings.append(f"{place.name} is likely crowded on a Saturday. Pick a quieter alternative or justify it.")
        if not place.indoor:
            rain = _rain_at(ctx, start)
            if rain >= 60:
                warnings.append(f"{place.name} is outdoors and rain chance at {to_hhmm(start)} is {rain}%. Make sure plan B covers it.")
            if sunset and end > to_min(sunset) + 20 and place.category in ("park", "garden", "lake", "viewpoint", "historic"):
                warnings.append(f"{place.name} runs past sunset ({sunset}). Parks may close or be dark.")
        if place.id.startswith("gen:"):
            warnings.append(f"'{place.name}' is a generic suggestion because live data was unavailable. Be upfront about it.")
    if unknown_hours:
        warnings.append(f"Opening hours unverified for: {', '.join(unknown_hours)}. Suggest checking before going.")

    if p.energy == "low" and len(rows) > 3:
        warnings.append(f"{len(rows)} stops is a lot for someone low on energy. Consider 2–3 relaxed stops.")
    wants_food = any(re.search(r"food|eat|cafe|coffee|dessert|brunch|dinner|lunch", i, re.I) for i in p.interests)
    if wants_food and not any(r["place"].kind == "food" for r in rows):
        warnings.append("User is interested in food but the plan has no food stop.")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "totals": {
            "start": to_hhmm(plan_start),
            "end": to_hhmm(plan_end),
            "minutes_used": used,
            "available_minutes": p.available_minutes,
            "travel_minutes": travel_min,
            "estimated_cost": total,
            "budget": round(budget),
            "currency": p.currency,
        },
    }
