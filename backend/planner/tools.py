"""The agent's tools: schemas the model sees, and the server-side code that runs them."""

from __future__ import annotations

import time
from typing import Any, Literal
from urllib.parse import quote_plus

from pydantic import BaseModel, Field, ValidationError

from .checks import RunContext, StopIn, estimate_cost, itinerary, to_hhmm, validate_plan
from .places import find_places
from .pricing import ACTIVITY_CATEGORIES, FOOD_CATEGORIES
from .weather import saturday_forecast

ActivityCategory = Literal[tuple(ACTIVITY_CATEGORIES)]  # type: ignore[valid-type]
FoodCategory = Literal[tuple(FOOD_CATEGORIES)]  # type: ignore[valid-type]


# ---------- tool inputs ----------


class WeatherIn(BaseModel):
    pass


class ActivitiesIn(BaseModel):
    categories: list[ActivityCategory] = Field(min_length=1, max_length=6, description="Activity categories to search.")
    radius_km: float = Field(default=4, ge=0.5, le=12, description="Search radius around the user's area (or near_place_id).")
    indoor_only: bool = Field(default=False, description="Only indoor places (use when rain is likely).")
    near_place_id: str | None = Field(default=None, description="Search around this place instead of the user's area.")


class FoodIn(BaseModel):
    categories: list[FoodCategory] = Field(min_length=1, max_length=5, description="Food categories to search.")
    radius_km: float = Field(default=2.5, ge=0.5, le=10)
    max_price_per_person: float | None = Field(default=None, ge=0, description="Skip places estimated well above this.")
    near_place_id: str | None = Field(default=None, description="Search around this place, e.g. the activity before the meal.")


class StopsIn(BaseModel):
    stops: list[StopIn] = Field(min_length=1, max_length=6)


class FinalStop(StopIn):
    activity: str = Field(description="What to do there, specific (e.g. 'Slow lakeside walk, then sit by the water').")
    why_it_fits: str = Field(description="1-2 sentences linking this stop to the user's mood, interests, constraints or the weather.")
    tip: str | None = Field(default=None, description="Optional practical tip (what to order, when to arrive, what to check).")


class PlanB(BaseModel):
    trigger: str = Field(description="When to switch, e.g. 'If it rains' or 'If the café is packed'.")
    suggestion: str = Field(description="What to do instead.")
    place_ids: list[str] = Field(default_factory=list, description="Real place_ids from searches used by plan B, if any.")


class SubmitIn(BaseModel):
    title: str = Field(description="Short, fun title for the plan.")
    summary: str = Field(description="2-3 sentences: the shape of the day and why it suits this person.")
    stops: list[FinalStop] = Field(min_length=1, max_length=6)
    tradeoffs: list[str] = Field(default_factory=list, description="Honest trade-offs, e.g. 'X is slightly over budget but quieter'.")
    plan_b: PlanB


def _inline_refs(schema: dict) -> dict:
    """Pydantic emits $defs/$ref; inline them so each tool schema is self-contained."""
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(dict(defs[node["$ref"].split("/")[-1]]))
            # drop pydantic's "title" annotations, but keep a property that happens to be named title
            return {k: walk(v) for k, v in node.items() if not (k == "title" and isinstance(v, str))}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def _tool(name: str, description: str, model: type[BaseModel]) -> dict:
    return {"name": name, "description": description, "parameters": _inline_refs(model.model_json_schema())}


TOOL_MODELS: dict[str, type[BaseModel]] = {
    "get_weather": WeatherIn,
    "find_activities": ActivitiesIn,
    "find_food": FoodIn,
    "estimate_cost": StopsIn,
    "validate_plan": StopsIn,
    "submit_plan": SubmitIn,
}

TOOL_SPECS = [
    _tool("get_weather", "Saturday's forecast for the user's city during their time window: hourly rain chance, temperature, sunset.", WeatherIn),
    _tool(
        "find_activities",
        "Find real places to do things near the user (OpenStreetMap, with curated fallback). Returns place_ids with distance, "
        "indoor flag, estimated cost per person, crowd estimate and opening hours when known.",
        ActivitiesIn,
    ),
    _tool(
        "find_food",
        "Find real cafés and restaurants near the user or near a chosen place. Honours the user's dietary hard constraints "
        "(vegetarian status is 'yes'/'only' when tagged, 'unverified' otherwise). Returns place_ids with estimated cost per person.",
        FoodIn,
    ),
    _tool(
        "estimate_cost",
        "Estimate the cost of a draft plan: per-stop spend plus travel between stops (walk or auto/cab), against the budget.",
        StopsIn,
    ),
    _tool(
        "validate_plan",
        "Check a draft plan against the user's time window, budget, hard constraints, travel distances, opening hours, crowds "
        "and weather. Returns errors (must fix) and warnings (fix or explain as a trade-off). Call before submit_plan.",
        StopsIn,
    ),
    _tool(
        "submit_plan",
        "Submit the final plan. This ends the task. It is re-validated; if it still has errors you'll get them back to fix.",
        SubmitIn,
    ),
]


# ---------- execution ----------


class ToolOutcome(BaseModel):
    result: dict
    summary: str
    source: str | None = None
    is_error: bool = False
    final_plan: dict | None = None


def _register(ctx: RunContext, places: list) -> None:
    for p in places:
        ctx.places[p.id] = p


def _center_for(ctx: RunContext, place_id: str | None):
    if place_id and place_id in ctx.places:
        p = ctx.places[place_id]
        return ctx.center.model_copy(update={"lat": p.lat, "lon": p.lon, "name": p.name})
    return ctx.center


async def run_tool(ctx: RunContext, name: str, raw: Any, submit_attempts: int) -> ToolOutcome:
    model = TOOL_MODELS.get(name)
    if not model:
        return ToolOutcome(result={"error": f"Unknown tool {name}"}, summary="unknown tool", is_error=True)
    try:
        args = model.model_validate(raw if isinstance(raw, dict) else {})
    except ValidationError as err:
        return ToolOutcome(
            result={"error": "Invalid input", "details": err.errors(include_url=False)[:5]},
            summary="invalid input, asked the model to retry",
            is_error=True,
        )

    p = ctx.prefs
    if isinstance(args, WeatherIn):
        try:
            ctx.weather = await saturday_forecast(ctx.city, p.start_time, p.available_minutes)
            return ToolOutcome(result=ctx.weather, summary=f"{ctx.weather['date']}: {ctx.weather['summary']}", source="live")
        except Exception as err:
            return ToolOutcome(
                result={"error": f"Weather service unavailable ({type(err).__name__}).", "advice": "Assume the weather is uncertain: mix indoor and outdoor and make plan B cover rain."},
                summary="weather unavailable, planning for uncertainty",
                source="unavailable",
            )

    if isinstance(args, (ActivitiesIn, FoodIn)):
        food = isinstance(args, FoodIn)
        places, source, notes = await find_places(
            kind="food" if food else "activity",
            categories=list(args.categories),
            center=_center_for(ctx, args.near_place_id),
            city=ctx.city.name,
            country_code=ctx.city.country_code,
            radius_km=args.radius_km,
            vegetarian=ctx.vegetarian,
            indoor_only=getattr(args, "indoor_only", False),
            max_price=getattr(args, "max_price_per_person", None),
            limit=10 if food else 12,
        )
        _register(ctx, places)
        label = {"live": "live OpenStreetMap", "fallback": "curated fallback", "generic": "generic fallback"}[source]
        return ToolOutcome(
            result={"source": source, "notes": notes, "currency": p.currency, "places": [x.brief() for x in places]},
            summary=f"{len(places)} {'food spots' if food else 'places'} ({label})",
            source=source,
        )

    if name == "estimate_cost":
        est = estimate_cost(ctx, args.stops)
        return ToolOutcome(result=est, summary=f"≈ {est['total']} {p.currency} of {est['budget']} budget")

    if name == "validate_plan":
        v = validate_plan(ctx, args.stops)
        return ToolOutcome(result=v, summary=_validation_summary(v), is_error=False)

    # submit_plan
    v = validate_plan(ctx, args.stops)
    if v["errors"] and submit_attempts < 2:
        return ToolOutcome(
            result={"accepted": False, "errors": v["errors"], "warnings": v["warnings"], "instruction": "Fix the errors and call submit_plan again."},
            summary=f"rejected: {len(v['errors'])} error(s) to fix",
            is_error=True,
        )
    plan = build_final_plan(ctx, args, v)
    return ToolOutcome(result={"accepted": True}, summary="plan accepted" + (" with warnings" if plan["warnings"] else ""), final_plan=plan)


def _validation_summary(v: dict) -> str:
    if not v.get("totals"):
        return f"{len(v['errors'])} error(s)"
    t = v["totals"]
    head = "passes" if v["ok"] else f"{len(v['errors'])} error(s)"
    return f"{head}, {len(v['warnings'])} warning(s); {t['start']}–{t['end']}, ≈ {t['estimated_cost']} {t['currency']}"


def _map_url(name: str, lat: float, lon: float, area: str) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(f'{name}, {area}')}"


def build_final_plan(ctx: RunContext, args: SubmitIn, v: dict) -> dict:
    rows, _ = itinerary(ctx, list(args.stops))
    by_id = {s.place_id: s for s in args.stops}
    area = ctx.city.name
    stops = []
    for r in rows:
        place, stop = r["place"], by_id[r["place"].id]
        stops.append({
            "start": to_hhmm(r["start"]),
            "end": to_hhmm(r["end"]),
            "name": place.name,
            "category": place.category,
            "activity": stop.activity,
            "why_it_fits": stop.why_it_fits,
            "tip": stop.tip,
            "est_cost": round(r["cost"]),
            "address": place.address,
            "indoor": place.indoor,
            "crowd": place.crowd,
            "veg": place.veg,
            "opening_hours": place.opening_hours,
            "source": place.source,
            "note": place.note,
            "map_url": _map_url(place.name, place.lat, place.lon, area),
            "travel_from_previous": r["leg"],
        })
    plan_b_places = [
        {"name": ctx.places[i].name, "map_url": _map_url(ctx.places[i].name, ctx.places[i].lat, ctx.places[i].lon, area)}
        for i in args.plan_b.place_ids if i in ctx.places
    ]
    est = estimate_cost(ctx, list(args.stops))
    sources = sorted({s["source"] for s in stops})
    return {
        "title": args.title,
        "summary": args.summary,
        "date": (ctx.weather or {}).get("date"),
        "location": ctx.center.label if ctx.center is not ctx.city else ctx.city.label,
        "weather": (ctx.weather or {}).get("summary"),
        "stops": stops,
        "totals": v.get("totals"),
        "cost": {"stops": est["stops"], "travel": est["travel"], "total": est["total"], "budget": est["budget"], "currency": est["currency"]},
        "tradeoffs": args.tradeoffs,
        "plan_b": {"trigger": args.plan_b.trigger, "suggestion": args.plan_b.suggestion, "places": plan_b_places},
        "warnings": v["errors"] + v["warnings"],
        "data_sources": sources,
        "generated_at": int(time.time()),
    }
