"""The agent loop. Yields trace events (dicts) that the API streams to the browser as SSE."""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import AsyncIterator

from google import genai
from google.genai import errors, types

from .checks import RunContext, StopIn, to_hhmm, to_min, validate_plan
from .geo import GeocodeError, haversine_km, resolve_location
from .models import PlanRequest
from .parse import parse_preferences
from .prompts import AGENT_SYSTEM
from .tools import TOOL_SPECS, FinalStop, PlanB, SubmitIn, build_final_plan, run_tool

# Tried in order; the run switches to the next model if one is overloaded before its first turn succeeds.
MODELS = [m.strip() for m in os.getenv("GEMINI_MODELS", "gemini-3.5-flash-lite,gemini-4-hydrogen,gemini-3.5-flash").split(",") if m.strip()]
MID_RUN_RETRIES = 3  # after the first good turn the history is tied to one model, so retry it with backoff
THINKING_LEVEL = {"gemini-4-hydrogen": "medium", "gemini-4-neon": "medium"}  # others: "low"
MAX_TURNS = 10
DEADLINE_S = 240  # Vercel Hobby functions stop at 300s
TURN_TIMEOUT_MS = 45_000

GEMINI_TOOLS = types.Tool(function_declarations=[
    types.FunctionDeclaration(name=t["name"], description=t["description"], parameters_json_schema=t["parameters"])
    for t in TOOL_SPECS
])

LABELS = {
    "get_weather": "Checking Saturday's weather",
    "find_activities": "Finding things to do",
    "find_food": "Finding food that fits",
    "estimate_cost": "Estimating cost",
    "validate_plan": "Checking the plan is realistic",
    "submit_plan": "Submitting the final plan",
}


class ModelBusy(Exception):
    """Overloaded, rate-limited or timed out: worth trying another model."""


def _ev(type_: str, **data) -> dict:
    return {"type": type_, **data}


class _Steps:
    def __init__(self) -> None:
        self.n = 0

    def start(self, name: str, label: str, input_: object = None) -> tuple[dict, str, float]:
        self.n += 1
        sid = f"s{self.n}"
        return _ev("step_start", id=sid, name=name, label=label, input=input_), sid, time.monotonic()

    @staticmethod
    def end(sid: str, t0: float, summary: str, ok: bool = True, source: str | None = None, output: object = None) -> dict:
        return _ev("step_end", id=sid, ok=ok, summary=summary, source=source, ms=round((time.monotonic() - t0) * 1000), output=output)


def _user_message(ctx: RunContext) -> str:
    p = ctx.prefs
    end = to_hhmm(to_min(p.start_time) + p.available_minutes)
    where = ctx.city.label + (f", around {ctx.center.name}" if ctx.center is not ctx.city else "")
    prefs = p.model_dump(exclude={"needs_clarification", "clarifying_questions"})
    return (
        f"Plan this Saturday in {where}.\n"
        f"Time window: {p.start_time} to {end} ({p.available_minutes} minutes).\n"
        f"Budget: {round(p.budget)} {p.currency} per person, covering tickets, food and local travel.\n"
        f"Preferences:\n{json.dumps(prefs, ensure_ascii=False, indent=1)}"
    )


async def run(req: PlanRequest) -> AsyncIterator[dict]:
    started = time.monotonic()
    steps = _Steps()
    client = genai.Client()  # reads GEMINI_API_KEY

    # 1. understand preferences
    ev, sid, t0 = steps.start("parse_preferences", "Understanding your preferences", req.model_dump(exclude_none=True, exclude_defaults=True))
    yield ev
    prefs, parse_source = await parse_preferences(client, req)
    yield steps.end(sid, t0, f"{prefs.city or 'no city'} · {prefs.available_minutes} min · {prefs.energy} energy" + (" (rule-based: AI unavailable)" if parse_source == "rules" else ""), source=parse_source, output=prefs.model_dump())
    yield _ev("preferences", preferences=prefs.model_dump(), source=parse_source)
    if prefs.needs_clarification:
        yield _ev("clarify", questions=prefs.clarifying_questions, assumptions=prefs.assumptions)
        return

    # 2. resolve the city / neighbourhood
    ev, sid, t0 = steps.start("resolve_location", "Finding your city on the map", {"city": prefs.city, "area": prefs.area})
    yield ev
    try:
        city, area = await resolve_location(prefs.city or "", prefs.area, prefs.country_code)
    except GeocodeError as err:
        yield steps.end(sid, t0, str(err), ok=False)
        yield _ev("clarify", questions=[f"I couldn't find '{prefs.city}'. Which city (and country) do you mean?"], assumptions=[])
        return
    except Exception as err:
        yield steps.end(sid, t0, f"Geocoding failed ({type(err).__name__})", ok=False)
        yield _ev("error", message="Couldn't reach the map service. Please try again in a moment.", retryable=True)
        return
    note = f"{city.label}" + (f" · around {area.name}" if area else (f" · '{prefs.area}' not found, using city centre" if prefs.area else ""))
    yield steps.end(sid, t0, note, source="live", output={"city": city.model_dump(), "area": area.model_dump() if area else None})
    if prefs.currency in ("local", "") and city.country_code == "IN":
        prefs.currency = "INR"

    ctx = RunContext(prefs=prefs, city=city, center=area or city)

    # 3. agent loop
    if parse_source == "rules":
        async for e in _rule_based_plan(ctx, steps, reason="The AI model is unavailable right now"):
            yield e
        return

    contents: list[types.Content] = [types.Content(role="user", parts=[types.Part(text=_user_message(ctx))])]
    model_idx, good_turns, retries, submit_attempts = 0, 0, 0, 0
    yield _ev("status", message=f"Agent model: {MODELS[model_idx]}", tone="info")
    turn = 0
    while turn < MAX_TURNS and time.monotonic() - started < DEADLINE_S:
        parts: list[types.Part] | None = None
        finish = None
        reason = ""
        try:
            async for item in _model_turn(client, MODELS[model_idx], contents, f"t{turn}"):
                if isinstance(item, dict):
                    yield item
                else:
                    parts, finish = item
        except ModelBusy as err:
            if good_turns == 0 and model_idx + 1 < len(MODELS):
                model_idx += 1
                yield _ev("status", message=f"{err}. Switching to {MODELS[model_idx]}.")
                continue
            if retries < MID_RUN_RETRIES:  # mid-run: history carries this model's thought signatures, so retry the same model
                retries += 1
                yield _ev("status", message=f"{err}. Retrying ({retries}/{MID_RUN_RETRIES})…", tone="info")
                await asyncio.sleep(1.5 * retries)
                continue
            reason = f"The AI model is overloaded ({err})"
        except errors.APIError as err:  # bad key, invalid request...
            reason = f"The AI model rejected the request ({err.code} {err.status})"
        except Exception as err:
            reason = f"The AI model stopped responding ({type(err).__name__})"
        if parts is None:
            async for e in _rule_based_plan(ctx, steps, reason=reason):
                yield e
            return

        good_turns += 1
        turn += 1
        retries = 0
        calls = [p.function_call for p in parts if p.function_call]
        if finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"):
            yield _ev("error", message="The model declined this request. Try rephrasing it.", retryable=False)
            return
        if finish == "MAX_TOKENS" and calls:
            yield _ev("error", message="The plan got too long to finish. Please try again.", retryable=True)
            return
        # Append the model's parts exactly as received: they carry thought signatures Gemini requires back.
        contents.append(types.Content(role="model", parts=parts))
        if not calls:
            contents.append(types.Content(role="user", parts=[types.Part(text="Please continue: validate your plan and finish by calling submit_plan.")]))
            continue

        # Run this turn's function calls concurrently, streaming start/end events.
        starts = []
        for fc in calls:
            ev, sid, t0 = steps.start(fc.name, LABELS.get(fc.name, fc.name), fc.args)
            starts.append((fc, sid, t0))
            yield ev
        outcomes = await asyncio.gather(*(run_tool(ctx, fc.name, fc.args or {}, submit_attempts) for fc, _, _ in starts))
        responses, final_plan = [], None
        for (fc, sid, t0), out in zip(starts, outcomes):
            yield steps.end(sid, t0, out.summary, ok=not out.is_error, source=out.source, output=out.result)
            payload = {"error": out.result} if out.is_error else out.result
            responses.append(types.Part(function_response=types.FunctionResponse(id=fc.id, name=fc.name, response=payload)))
            if fc.name == "submit_plan":
                submit_attempts += 1
                final_plan = out.final_plan or final_plan
        if final_plan:
            yield _ev("plan", plan=final_plan, mode="agent", model=MODELS[model_idx])
            yield _ev("done", seconds=round(time.monotonic() - started, 1))
            return
        contents.append(types.Content(role="user", parts=responses))

    async for e in _rule_based_plan(ctx, steps, reason="The agent ran out of time before finishing"):
        yield e


async def _model_turn(client: genai.Client, model: str, contents: list[types.Content], turn_id: str):
    """Stream one model turn. Yields thought events, then (parts, finish_reason)."""
    config = types.GenerateContentConfig(
        system_instruction=AGENT_SYSTEM,
        tools=[GEMINI_TOOLS],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        thinking_config=types.ThinkingConfig(include_thoughts=True, thinking_level=THINKING_LEVEL.get(model, "low")),
        max_output_tokens=16_000,
        http_options=types.HttpOptions(timeout=TURN_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1)),
    )
    parts: list[types.Part] = []
    finish = None
    block, block_kind, n = None, None, 0
    yield _ev("thought_start", block=f"{turn_id}-0", kind="status")
    yield _ev("thought_delta", block=f"{turn_id}-0", text=f"Thinking with {model}…")
    try:
        stream = await client.aio.models.generate_content_stream(model=model, contents=contents, config=config)
        async for chunk in stream:
            cand = chunk.candidates[0] if chunk.candidates else None
            if not cand:
                continue
            if cand.finish_reason:
                finish = cand.finish_reason.value if hasattr(cand.finish_reason, "value") else str(cand.finish_reason)
            for part in (cand.content.parts if cand.content and cand.content.parts else []):
                parts.append(part)  # keep every part, even empty ones: one may hold the thought signature
                if part.text and not part.function_call:
                    kind = "thought" if part.thought else "text"
                    if kind != block_kind:
                        n += 1
                        block, block_kind = f"{turn_id}-{n}", kind
                        yield _ev("thought_start", block=block, kind=kind)
                    yield _ev("thought_delta", block=block, text=part.text)
    except errors.ServerError as err:
        raise ModelBusy(f"{model} is unavailable ({err.code})") from err
    except errors.ClientError as err:
        if err.code == 429:
            raise ModelBusy(f"{model} is rate-limited (429)") from err
        raise
    except (asyncio.TimeoutError, TimeoutError) as err:
        raise ModelBusy(f"{model} timed out") from err
    except Exception as err:
        if "timeout" in type(err).__name__.lower():
            raise ModelBusy(f"{model} timed out") from err
        raise
    if not parts:
        raise ModelBusy(f"{model} returned an empty response")
    yield (parts, finish)


async def _rule_based_plan(ctx: RunContext, steps: _Steps, reason: str) -> AsyncIterator[dict]:
    """Graceful degradation: build a simple plan with the same tools but no LLM."""
    yield _ev("status", message=f"{reason}. Building a simpler rule-based plan from the same data.")
    p = ctx.prefs
    rainy = False
    for name, args in (
        ("get_weather", {}),
        ("find_activities", None),
        ("find_food", None),
    ):
        if args is None:
            if name == "find_activities":
                cats = _categories_for(p.interests, rainy)
                args = {"categories": cats, "radius_km": 4, "indoor_only": rainy}
            else:
                args = {"categories": ["cafe", "restaurant"], "radius_km": 3, "max_price_per_person": p.budget * 0.5}
        ev, sid, t0 = steps.start(name, LABELS[name], args)
        yield ev
        out = await run_tool(ctx, name, args, 0)
        yield steps.end(sid, t0, out.summary, ok=not out.is_error, source=out.source, output=out.result)
        if name == "get_weather" and ctx.weather:
            rainy = ctx.weather.get("max_rain_chance_in_window", 0) >= 60

    activities = [x for x in ctx.places.values() if x.kind == "activity" and not (ctx.avoid_crowds and x.crowd == "high")]
    foods = [x for x in ctx.places.values() if x.kind == "food" and x.veg != "no"]
    if rainy:
        activities = [a for a in activities if a.indoor] or activities
    if not activities or not foods:
        yield _ev("error", message="Couldn't find enough places to build a plan. Try a bigger city or wider interests.", retryable=True)
        return
    act = min(activities, key=lambda a: a.distance_km)
    food = min(foods, key=lambda f: (f.veg not in ("yes", "only"), haversine_km(act.lat, act.lon, f.lat, f.lon)))

    start = to_min(p.start_time)
    act_minutes = max(45, min(120, int(p.available_minutes * 0.45)))
    gap = 30
    act_reasons = [f"only {act.distance_km:.1f} km away"]
    if rainy and act.indoor:
        act_reasons.append("indoors, so the forecast rain won't matter")
    if p.energy == "low":
        act_reasons.append("low-effort for a tired day")
    if ctx.avoid_crowds and act.crowd == "low":
        act_reasons.append("usually quiet")
    food_reasons = [f"{haversine_km(act.lat, act.lon, food.lat, food.lon) * 1.35:.1f} km from the first stop"]
    if ctx.vegetarian:
        food_reasons.append("vegetarian-friendly" if food.veg in ("yes", "only") else "veg status unverified, so check the menu")
    if food.est_cost <= p.budget * 0.4:
        food_reasons.append("comfortably within budget")
    stops = [
        FinalStop(place_id=act.id, start_time=to_hhmm(start), duration_minutes=act_minutes,
                  activity=f"Unhurried time at {act.name}", why_it_fits=f"{act.name} is {', '.join(act_reasons)}.", tip=None),
        FinalStop(place_id=food.id, start_time=to_hhmm(start + act_minutes + gap), duration_minutes=60,
                  activity=f"Eat at {food.name}", why_it_fits=f"{food.name} is {', '.join(food_reasons)}.", tip=None),
    ]
    stop_in = [StopIn(**s.model_dump(include={"place_id", "start_time", "duration_minutes"})) for s in stops]
    ev, sid, t0 = steps.start("validate_plan", LABELS["validate_plan"], {"stops": [s.model_dump() for s in stop_in]})
    yield ev
    v = validate_plan(ctx, stop_in)
    yield steps.end(sid, t0, ("passes" if v["ok"] else f"{len(v['errors'])} error(s)") + f", {len(v['warnings'])} warning(s)", ok=v["ok"], output=v)

    alt = next((a for a in activities if a.indoor and a.id != act.id), None)
    submit = SubmitIn(
        title=f"A simple Saturday around {ctx.center.name}",
        summary=f"{reason}, so this is a simpler rule-based plan: one nearby activity and a meal close to it.",
        stops=stops,
        tradeoffs=["Built by rules, not the AI agent, so it's less personalised than usual."],
        plan_b=PlanB(trigger="If it rains or a place is closed", suggestion=f"Head to {alt.name} instead." if alt else "Pick an indoor café nearby.", place_ids=[alt.id] if alt else []),
    )
    yield _ev("plan", plan=build_final_plan(ctx, submit, v), mode="rules")
    yield _ev("done")


def _categories_for(interests: list[str], rainy: bool) -> list[str]:
    text = " ".join(interests).lower()
    cats = []
    for words, cat in (
        (("walk", "nature", "outdoor", "park"), "park"), (("walk", "lake", "water"), "lake"),
        (("music", "gig", "concert"), "live_music"), (("art", "culture"), "gallery"), (("museum", "history"), "museum"),
        (("book", "read"), "bookstore"), (("movie", "film"), "cinema"), (("game", "fun"), "arcade"), (("theatre", "play"), "theatre"),
    ):
        if any(w in text for w in words) and cat not in cats:
            cats.append(cat)
    if rainy:
        cats = [c for c in cats if c not in ("park", "lake")] + ["bookstore", "museum"]
    return (cats or ["park", "bookstore", "gallery"])[:5]
