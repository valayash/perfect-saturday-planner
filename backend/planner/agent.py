"""The agent loop. Yields trace events (dicts) that the API streams to the browser as SSE.

Provider chain: Vercel AI Gateway (several models) -> Gemini API (several models) -> rule-based planner.
Each stage hands the work gathered so far to the next, so nothing is fetched twice.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

from google import genai
from google.genai import errors, types
from openai import AsyncOpenAI

from .checks import RunContext, StopIn, to_hhmm, to_min, validate_plan
from .gateway import GATEWAY_MODELS, OPENAI_TOOLS, error_text, gateway_client, is_account_problem, is_busy
from .geo import GeocodeError, haversine_km, resolve_location
from .models import PlanRequest
from .parse import parse_preferences
from .prompts import AGENT_SYSTEM
from .tools import TOOL_SPECS, FinalStop, PlanB, SubmitIn, build_final_plan, run_tool

# Gemini models, tried in order; the run switches to the next one if a model is overloaded.
MODELS = [m.strip() for m in os.getenv("GEMINI_MODELS", "gemini-3.5-flash-lite,gemini-3.8-flash,gemini-4-hydrogen").split(",") if m.strip()]
MID_RUN_RETRIES = 2  # then hand off to the next model with a summary (Gemini history is tied to one model's thought signatures)
THINKING_LEVEL = {"gemini-4-hydrogen": "medium", "gemini-4-neon": "medium"}  # others: "low"
MAX_TURNS = 10
DEADLINE_S = 240  # Vercel Hobby functions stop at 300s
TURN_TIMEOUT_MS = 20_000
GATEWAY_TURN_TIMEOUT_S = 45  # reasoning models on long turns need more than the Gemini fail-fast budget
NUDGE = "Please continue: validate your plan and finish by calling submit_plan."

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


@dataclass
class LoopState:
    """Shared across provider stages so a later stage can pick up where an earlier one stopped."""

    started: float
    submit_attempts: int = 0
    last_draft: dict | None = None
    final_plan: dict | None = None
    model: str | None = None
    failure: str = ""
    attempt: int = 0
    results: list = field(default_factory=list)

    def out_of_time(self) -> bool:
        return time.monotonic() - self.started > DEADLINE_S


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


def _handoff_message(ctx: RunContext, last_draft: dict | None) -> str:
    """Seed a different model with everything gathered so far (place_ids stay valid: they live in ctx.places)."""
    work = {
        "weather": ctx.weather,
        "places_found": [p.brief() for p in list(ctx.places.values())[:40]],
        "last_draft": last_draft,
    }
    return (
        _user_message(ctx)
        + "\n\nAnother planner already gathered the data below before it became unavailable. Use these place_ids "
        "(you may call tools again if you need more), then validate the plan and finish by calling submit_plan.\n"
        + json.dumps(work, ensure_ascii=False)
    )


def _first_message(ctx: RunContext, state: LoopState) -> str:
    return _handoff_message(ctx, state.last_draft) if ctx.places else _user_message(ctx)


def _thinking(state: LoopState, model: str) -> list[dict]:
    state.attempt += 1
    block = f"a{state.attempt}-0"
    return [_ev("thought_start", block=block, kind="status"), _ev("thought_delta", block=block, text=f"Thinking with {model}…")]


async def _run_calls(ctx: RunContext, steps: _Steps, state: LoopState, calls: list[tuple[str | None, str, dict]]) -> AsyncIterator[dict]:
    """Run one turn's tool calls concurrently, streaming start/end events. Results land in state.results."""
    starts = []
    for call_id, name, args in calls:
        ev, sid, t0 = steps.start(name, LABELS.get(name, name), args)
        starts.append((call_id, name, args, sid, t0))
        yield ev
    outcomes = await asyncio.gather(*(run_tool(ctx, name, args, state.submit_attempts) for _, name, args, _, _ in starts))
    state.results = []
    for (call_id, name, args, sid, t0), out in zip(starts, outcomes):
        yield steps.end(sid, t0, out.summary, ok=not out.is_error, source=out.source, output=out.result)
        state.results.append((call_id, name, {"error": out.result} if out.is_error else out.result))
        if name in ("validate_plan", "estimate_cost", "submit_plan") and not out.final_plan:
            state.last_draft = {"tool": name, "input": args, "result": out.result}
        if name == "submit_plan":
            state.submit_attempts += 1
            state.final_plan = out.final_plan or state.final_plan


async def run(req: PlanRequest, oidc_token: str | None = None) -> AsyncIterator[dict]:
    started = time.monotonic()
    steps = _Steps()
    gateway = gateway_client(oidc_token)
    try:
        gemini: genai.Client | None = genai.Client()  # reads GEMINI_API_KEY
    except Exception:  # no API key configured
        gemini = None

    # 1. understand preferences
    ev, sid, t0 = steps.start("parse_preferences", "Understanding your preferences", req.model_dump(exclude_none=True, exclude_defaults=True))
    yield ev
    prefs, parse_source = await parse_preferences(gemini, req, gateway)
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
    state = LoopState(started=started)

    # 3. agent loop: AI Gateway, then Gemini, then rules
    if parse_source != "rules":
        stages = []
        if gateway and parse_source != "gemini":  # if parsing already fell through to Gemini, the gateway is down
            stages.append(_gateway_loop(gateway, ctx, steps, state))
        if gemini:
            stages.append(_gemini_loop(gemini, ctx, steps, state))
        for stage in stages:
            async for e in stage:
                yield e
            if state.final_plan:
                yield _ev("plan", plan=state.final_plan, mode="agent", model=state.model)
                yield _ev("done", seconds=round(time.monotonic() - started, 1))
                return
            if state.out_of_time():
                break
    reason = state.failure or ("The agent ran out of time before finishing" if state.out_of_time() else "The AI model is unavailable right now")
    async for e in _rule_based_plan(ctx, steps, reason=reason):
        yield e


async def _gateway_loop(client: AsyncOpenAI, ctx: RunContext, steps: _Steps, state: LoopState) -> AsyncIterator[dict]:
    """OpenAI-format tool loop through Vercel AI Gateway. Plain-JSON history, so switching models mid-run is safe."""
    messages: list[dict] = [{"role": "system", "content": AGENT_SYSTEM}, {"role": "user", "content": _first_message(ctx, state)}]
    model_idx, retries, turns = 0, 0, 0
    yield _ev("status", message=f"Agent model: {GATEWAY_MODELS[0]} via Vercel AI Gateway", tone="info")
    while turns < MAX_TURNS and not state.out_of_time():
        model = GATEWAY_MODELS[model_idx]
        for e in _thinking(state, model):
            yield e
        try:
            extra = {"reasoning_effort": "low"} if model.startswith("openai/gpt-oss") else {}  # faster turns, the validator checks the work
            resp = await client.chat.completions.create(
                model=model, messages=messages, tools=OPENAI_TOOLS, max_tokens=8000, timeout=GATEWAY_TURN_TIMEOUT_S, **extra
            )
        except Exception as err:
            if is_account_problem(err):
                state.failure = f"Vercel AI Gateway refused the request ({error_text(err)})"
                yield _ev("status", message=f"{state.failure}. Trying the Gemini API instead.")
                return
            if is_busy(err) and retries < 1:
                retries += 1
                yield _ev("status", message=f"{model} is busy ({error_text(err)}). Retrying…", tone="info")
                await asyncio.sleep(1.5)
                continue
            if model_idx + 1 < len(GATEWAY_MODELS):  # JSON history works with any model: just switch
                model_idx, retries = model_idx + 1, 0
                yield _ev("status", message=f"{model} failed ({error_text(err)}). Switching to {GATEWAY_MODELS[model_idx]}.")
                continue
            state.failure = f"Every AI Gateway model failed ({error_text(err)})"
            yield _ev("status", message=f"{state.failure}. Trying the Gemini API instead.")
            return
        retries, turns = 0, turns + 1
        choice = resp.choices[0]
        msg = choice.message
        reasoning = (msg.model_extra or {}).get("reasoning")
        for kind, text in (("thought", reasoning), ("text", msg.content)):
            if isinstance(text, str) and text.strip():
                block = f"a{state.attempt}-{kind}"
                yield _ev("thought_start", block=block, kind=kind)
                yield _ev("thought_delta", block=block, text=text.strip())
        tool_calls = msg.tool_calls or []
        if choice.finish_reason == "content_filter":
            state.failure = f"{model} declined the request"
            return
        assistant: dict = {"role": "assistant", "content": msg.content or ""}
        if tool_calls:
            assistant["tool_calls"] = [tc.model_dump(exclude_none=True) for tc in tool_calls]
        messages.append(assistant)
        if not tool_calls:
            messages.append({"role": "user", "content": NUDGE})
            continue
        calls = []
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_invalid_json": tc.function.arguments}
            calls.append((tc.id, tc.function.name, args if isinstance(args, dict) else {}))
        async for e in _run_calls(ctx, steps, state, calls):
            yield e
        if state.final_plan:
            state.model = model
            return
        for call_id, _, payload in state.results:
            messages.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps(payload, ensure_ascii=False)})
    if not state.failure:
        state.failure = "The AI Gateway agent didn't finish in time"


async def _gemini_loop(client: genai.Client, ctx: RunContext, steps: _Steps, state: LoopState) -> AsyncIterator[dict]:
    """Gemini function-calling loop (google-genai). Model parts are appended verbatim: they carry thought signatures."""
    contents: list[types.Content] = [types.Content(role="user", parts=[types.Part(text=_first_message(ctx, state))])]
    model_idx, good_turns, retries, turns = 0, 0, 0, 0
    yield _ev("status", message=f"Agent model: {MODELS[model_idx]}", tone="info")
    while turns < MAX_TURNS and not state.out_of_time():
        parts: list[types.Part] | None = None
        finish = None
        try:
            async for item in _model_turn(client, MODELS[model_idx], contents, state):
                if isinstance(item, dict):
                    yield item
                else:
                    parts, finish = item
        except ModelBusy as err:
            if good_turns == 0 and model_idx + 1 < len(MODELS):
                model_idx += 1
                yield _ev("status", message=f"{err}. Switching to {MODELS[model_idx]}.")
                continue
            if retries < MID_RUN_RETRIES:
                retries += 1
                yield _ev("status", message=f"{err}. Retrying ({retries}/{MID_RUN_RETRIES})…", tone="info")
                await asyncio.sleep(1.5 * retries)
                continue
            if model_idx + 1 < len(MODELS):  # hand off: fresh conversation for the next model, seeded with the work so far
                model_idx, retries, good_turns = model_idx + 1, 0, 0
                contents = [types.Content(role="user", parts=[types.Part(text=_handoff_message(ctx, state.last_draft))])]
                yield _ev("status", message=f"{err}. Handing the work so far to {MODELS[model_idx]}.")
                continue
            state.failure = f"The AI model is overloaded ({err})"
            return
        except errors.APIError as err:  # bad key, invalid request...
            state.failure = f"The AI model rejected the request ({err.code} {err.status})"
            return
        except Exception as err:
            state.failure = f"The AI model stopped responding ({type(err).__name__})"
            return

        good_turns, turns, retries = good_turns + 1, turns + 1, 0
        calls = [p.function_call for p in parts if p.function_call]
        if finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"):
            state.failure = "The model declined this request"
            return
        if finish == "MAX_TOKENS" and calls:
            state.failure = "The plan got too long for the model to finish"
            return
        contents.append(types.Content(role="model", parts=parts))
        if not calls:
            contents.append(types.Content(role="user", parts=[types.Part(text=NUDGE)]))
            continue
        async for e in _run_calls(ctx, steps, state, [(fc.id, fc.name, fc.args or {}) for fc in calls]):
            yield e
        if state.final_plan:
            state.model = MODELS[model_idx]
            return
        contents.append(types.Content(role="user", parts=[
            types.Part(function_response=types.FunctionResponse(id=call_id, name=name, response=payload))
            for call_id, name, payload in state.results
        ]))
    if not state.failure:
        state.failure = "The Gemini agent didn't finish in time"


async def _model_turn(client: genai.Client, model: str, contents: list[types.Content], state: LoopState):
    """Stream one Gemini turn. Yields thought events, then (parts, finish_reason)."""
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
    for e in _thinking(state, model):
        yield e
    turn_id = f"a{state.attempt}"
    block, block_kind, n = None, None, 0
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
