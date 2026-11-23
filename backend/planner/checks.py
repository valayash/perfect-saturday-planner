"""Deterministic cost estimation and plan validation (no LLM involved)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .geo import travel_leg
from .hours import open_during, saturday_intervals
from .models import Location, Place, Preferences

BUFFER_RATE = 0.05  # a little slack for tips, a coffee, price drift
LEVELS = ["low", "medium", "high"]


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

    @property
    def start_label(self) -> str:
        return self.center.name if self.center is not self.city else f"{self.city.name} centre"


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


def crowd_at(place: Place, minute: int) -> str:
    """Saturday crowd heuristic: the place's baseline, nudged by time of day. Always labelled an estimate."""
    level = LEVELS.index(place.crowd)
    hour = (minute // 60) % 24
    cat = place.category
    if cat in ("restaurant", "street_food", "fast_food"):
        level += 1 if (12 <= hour < 14 or 19 <= hour < 22) else -1 if 15 <= hour < 18 else 0
    elif cat in ("park", "garden", "lake", "viewpoint"):
        level += 1 if 16 <= hour < 19 else -1 if 12 <= hour < 15 else 0
    elif cat in ("mall", "market", "cinema", "arcade", "bowling"):
        level += 1 if 16 <= hour < 21 else -1 if hour < 12 else 0
    elif cat in ("live_music", "theatre"):
        level += 1 if 19 <= hour < 23 else -1 if hour < 17 else 0
    elif cat in ("museum", "gallery", "historic"):
        level += 1 if 11 <= hour < 15 else -1 if hour >= 16 else 0
    return LEVELS[max(0, min(2, level))]


def itinerary(ctx: RunContext, stops: list[StopIn]) -> tuple[list[dict], list[str]]:
    """Resolve stops against known places and compute travel legs between them. Returns (rows, unknown_ids)."""
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
            "crowd": crowd_at(place, start),
        })
        prev = place
    return rows, unknown


def trip_legs(ctx: RunContext, rows: list[dict]) -> tuple[dict | None, dict | None]:
    """Getting from the user's area to the first stop, and from the last stop back home."""
    if not rows:
        return None, None
    c, first, last = ctx.center, rows[0]["place"], rows[-1]["place"]
    return (
        travel_leg(c.lat, c.lon, first.lat, first.lon, ctx.city.country_code),
        travel_leg(last.lat, last.lon, c.lat, c.lon, ctx.city.country_code),
    )


def estimate_cost(ctx: RunContext, stops: list[StopIn]) -> dict:
    rows, unknown = itinerary(ctx, stops)
    start_leg, home_leg = trip_legs(ctx, rows)
    stop_costs = [{"name": r["place"].name, "cost": round(r["cost"])} for r in rows]
    legs = ([{"to": rows[0]["place"].name, "from": ctx.start_label, **start_leg}] if start_leg else [])
    legs += [{"to": r["place"].name, **r["leg"]} for r in rows if r["leg"]]
    legs += ([{"to": f"home ({ctx.start_label})", **home_leg}] if home_leg else [])
    places_total = sum(c["cost"] for c in stop_costs)
    travel_total = sum(leg["cost"] for leg in legs)
    buffer = round((places_total + travel_total) * BUFFER_RATE)
    total = places_total + travel_total + buffer
    budget = ctx.prefs.budget
    return {
        "currency": ctx.prefs.currency,
        "stops": stop_costs,
        "travel": legs,
        "places_total": round(places_total),
        "travel_total": round(travel_total),
        "buffer": buffer,
        "total": round(total),
        "budget": round(budget),
        "remaining": round(budget - total),
        "unknown_place_ids": unknown,
        "note": f"Category price estimates. Includes travel from {ctx.start_label}, the ride home and a 5% buffer.",
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
    start_leg, home_leg = trip_legs(ctx, rows)

    if plan_start < window_start - 15:
        errors.append(f"Plan starts at {to_hhmm(plan_start)} but the user is only free from {p.start_time}.")
    arrive = window_start + start_leg["minutes"]
    if plan_start < arrive - 20:
        errors.append(
            f"From {ctx.start_label} it takes ~{start_leg['minutes']} min to reach {rows[0]['place'].name}, "
            f"so the first stop can't start before ~{to_hhmm(arrive)}."
        )
    elif plan_start < arrive - 5:
        warnings.append(f"The first stop starts a little early: it's ~{start_leg['minutes']} min from {ctx.start_label}.")
    if plan_end > window_end + 15:
        errors.append(f"Plan ends at {to_hhmm(plan_end)}, past the {to_hhmm(window_end)} cutoff ({p.available_minutes} min available).")
    used = plan_end - max(plan_start, window_start)
    if used < p.available_minutes * 0.4:
        errors.append(
            f"Plan only uses {used} of the {p.available_minutes} minutes the user has. Add another stop or lengthen stops "
            "(free options such as parks, promenades, galleries or bookshops work on a tight budget)."
        )
    elif used < p.available_minutes * 0.6:
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
        warnings.append(f"{travel_min} min of the plan is travel between stops. Consider fewer or closer stops.")

    est = estimate_cost(ctx, stops)
    total, budget = est["total"], p.budget
    if total > budget * 1.15:
        errors.append(f"Estimated total {total} {p.currency} (incl. travel and a 5% buffer) is well over the {round(budget)} budget.")
    elif total > budget:
        warnings.append(f"Estimated total {total} {p.currency} is slightly over the {round(budget)} budget. Explain the trade-off or trim.")

    sunset = (ctx.weather or {}).get("sunset")
    unknown_hours = []
    for r in rows:
        place, start, end = r["place"], r["start"], r["end"]
        is_open = open_during(place.opening_hours, start, end)
        if is_open is False:
            spans = saturday_intervals(place.opening_hours) or []
            if not spans:
                errors.append(f"{place.name} is closed on Saturdays (hours: {place.opening_hours}). Swap it for another place.")
            else:
                a, b = max(spans, key=lambda ab: ab[1] - ab[0])
                latest = b - (end - start)
                fix = f"start it by {to_hhmm(latest)} for a {end - start}-min visit" if latest >= max(a, window_start) else "shorten it"
                errors.append(
                    f"{place.name} is open {to_hhmm(a)}–{to_hhmm(b)} on Saturday, but this visit runs {to_hhmm(start)}–{to_hhmm(end)} "
                    f"(after travel). Move it earlier ({fix}) or swap it for another place."
                )
        elif is_open is None and not place.id.startswith("gen:"):
            unknown_hours.append(place.name)
        if place.kind == "food" and end - start > 120:
            errors.append(f"{end - start} min at {place.name} is too long for a meal or café; keep it under 2 hours and add another stop.")
        if ctx.vegetarian and place.kind == "food":
            if place.veg == "no":
                errors.append(f"{place.name} is tagged as not vegetarian, which breaks a hard constraint.")
            elif place.veg == "unverified":
                warnings.append(f"{place.name} isn't tagged vegetarian. Tell the user to confirm veg options.")
        if ctx.avoid_crowds and r["crowd"] == "high":
            warnings.append(f"{place.name} is likely busy at {to_hhmm(start)} on a Saturday. Pick a quieter time or place, or justify it.")
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

    home_at = plan_end + home_leg["minutes"]
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "totals": {
            "start": to_hhmm(plan_start),
            "end": to_hhmm(plan_end),
            "home_at": to_hhmm(home_at),
            "minutes_used": used,
            "available_minutes": p.available_minutes,
            "travel_minutes": travel_min,
            "estimated_cost": total,
            "budget": round(budget),
            "currency": p.currency,
        },
    }
