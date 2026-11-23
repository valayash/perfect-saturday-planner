"""parse_preferences: Gemini structured output, with a rule-based fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time

from google import genai
from google.genai import types

from .fallback_data import CITY_KEYS
from .models import PlanRequest, Preferences
from .prompts import PARSE_SYSTEM

log = logging.getLogger("planner")
HARD_HINTS = re.compile(r"veg|vegan|jain|halal|kosher|allerg|gluten|wheelchair|accessib|kid|child|no alcohol|sober", re.I)


def _minutes(text: str | None) -> int:
    if not text:
        return 240
    t = text.lower()
    if "full day" in t or "whole day" in t:
        return 600
    if "half day" in t or "half a day" in t:
        return 240
    hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b", t)
    mins = re.search(r"(\d+)\s*(?:m|min|mins|minutes)\b", t)
    total = (float(hours.group(1)) * 60 if hours else 0) + (int(mins.group(1)) if mins else 0)
    if not total and re.fullmatch(r"\s*\d+(?:\.\d+)?\s*", t):
        total = float(t) * 60
    return int(min(max(total or 240, 60), 720))


def _energy(mood: str) -> str:
    if re.search(r"tired|exhaust|lazy|sleepy|drained|relax|chill|hungover|low", mood, re.I):
        return "low"
    if re.search(r"excit|energ|adventur|pumped|active|wild", mood, re.I):
        return "high"
    return "medium"


INDIAN_CITIES = set(CITY_KEYS) | {"pune", "hyderabad", "chennai", "kolkata", "ahmedabad", "jaipur", "goa", "gurugram", "gurgaon", "noida", "kochi", "chandigarh", "lucknow"}
INTEREST_WORDS = {
    "food": r"food|eat|restaurant|lunch|dinner|brunch", "coffee": r"coffee|caf[eé]", "music": r"music|gig|concert|jazz",
    "walks": r"walk|stroll|park|nature|outdoor", "art": r"\bart\b|gallery|museum", "books": r"book|read",
    "history": r"histor|heritage|fort|palace", "movies": r"movie|film|cinema", "games": r"game|arcade|bowling",
    "dessert": r"dessert|ice cream|sweet", "shopping": r"shop|market",
}
CONSTRAINT_WORDS = {"vegetarian": r"\bveg(etarian)?\b", "vegan": r"vegan", "jain food": r"\bjain\b", "avoid crowded places": r"crowd|quiet|calm", "no alcohol": r"no alcohol|sober|don'?t drink"}


def _split_city(text: str) -> tuple[str | None, str | None]:
    """'Pune, around Koregaon Park' -> ('Pune', 'Koregaon Park')."""
    text = text.strip().strip(".")
    if not text:
        return None, None
    city, _, rest = text.partition(",")
    area = re.sub(r"^\s*(?:around|near|in|at)\s+", "", rest, flags=re.I).strip() or None
    return city.strip() or None, area


def heuristic_preferences(req: PlanRequest) -> Preferences:
    """Rule-based parse used when the LLM is unavailable: the structured form, plus keywords from free text and answers."""
    is_where = lambda q: bool(re.search(r"\bcity\b|where", q, re.I))  # noqa: E731
    answers = " ".join(c.answer for c in req.clarifications if not is_where(c.question))
    blob = f"{req.free_text or ''} {answers}"  # keyword scan source (place names excluded)

    city, area = (req.city.strip() if req.city else None), req.area
    for c in req.clarifications:
        if not city and is_where(c.question):
            city, area = _split_city(c.answer)[0], area or _split_city(c.answer)[1]
    if not city and req.free_text:
        m = re.search(r"\b(?:in|at|around)\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)", req.free_text)
        city = m.group(1) if m else None

    budget = req.budget
    m = re.search(r"(?:₹|rs\.?|inr|\$|£|€)\s*(\d[\d,]*)|(\d[\d,]*)\s*(?:rupees|rs\b|inr|bucks|dollars|usd|eur|gbp)", blob, re.I)
    if not budget and m:
        budget = float((m.group(1) or m.group(2)).replace(",", ""))

    mood = req.mood or (req.free_text or "")[:120] or "wants a nice day out"
    minutes = _minutes(req.available_time or (blob if re.search(r"\d\s*(h|hour)|half day|full day", blob, re.I) else None))
    energy = _energy(f"{mood} {answers}")
    india = bool(city and city.lower() in INDIAN_CITIES) or bool(re.search(r"₹|rupee|\binr\b", blob, re.I))
    interests = [i.strip().lower() for i in req.interests if i.strip()]
    interests += [k for k, rx in INTEREST_WORDS.items() if k not in interests and re.search(rx, blob, re.I)]
    cons = [c.strip() for c in req.constraints if c.strip()]
    cons += [k for k, rx in CONSTRAINT_WORDS.items() if k not in cons and re.search(rx, blob, re.I)]
    start = req.start_time or ("15:00" if energy == "low" and minutes <= 300 else "11:00" if minutes >= 360 else "14:00")

    assumptions = []
    if not budget:
        assumptions.append("Assumed a budget of 1500.")
    if not req.available_time:
        assumptions.append(f"Assumed {minutes // 60} hours.")
    if not req.start_time:
        assumptions.append(f"Assumed a {start} start.")
    return Preferences(
        city=city,
        country_code="IN" if india else None,
        area=area,
        budget=budget or 1500,
        currency="INR" if india else "local",
        available_minutes=minutes,
        start_time=start,
        mood=mood,
        energy=energy,
        interests=interests or ["food", "walks"],
        hard_constraints=[c for c in cons if HARD_HINTS.search(c)],
        soft_constraints=[c for c in cons if not HARD_HINTS.search(c)],
        assumptions=assumptions,
        needs_clarification=not city,
        clarifying_questions=[] if city else ["Which city will you be in on Saturday?"],
    )


def _request_text(req: PlanRequest) -> str:
    fields = req.model_dump(exclude={"free_text", "clarifications"}, exclude_none=True)
    fields = {k: v for k, v in fields.items() if v not in ([], "")}
    parts = [f"Structured fields: {json.dumps(fields, ensure_ascii=False)}"]
    if req.free_text:
        parts.append(f"Free text from the user:\n<user_text>\n{req.free_text}\n</user_text>")
    if req.clarifications:
        qa = "\n".join(f"Q: {c.question}\nA: {c.answer}" for c in req.clarifications)
        parts.append(f"Answers to earlier clarifying questions:\n{qa}")
    return "\n\n".join(parts)


PARSE_MODEL = os.getenv("GEMINI_PARSE_MODEL", "gemini-3.5-flash-lite")
PARSE_BUDGET_S = 20


async def parse_preferences(client: genai.Client | None, req: PlanRequest) -> tuple[Preferences, str]:
    """Returns (preferences, source) where source is 'gemini' or 'rules'."""
    fallbacks = [m.strip() for m in os.getenv("GEMINI_MODELS", "gemini-4-hydrogen").split(",") if m.strip()]
    prefs, source = None, "rules"
    deadline = time.monotonic() + PARSE_BUDGET_S  # an overloaded Gemini should cost seconds, not a minute
    models = dict.fromkeys([PARSE_MODEL, *fallbacks]) if client else {}  # fast model first, then the rest of the chain
    for model in models:
        remaining = deadline - time.monotonic()
        if remaining < 3:
            break
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model,
                    contents=_request_text(req),
                    config=types.GenerateContentConfig(
                        system_instruction=PARSE_SYSTEM,
                        response_mime_type="application/json",
                        response_json_schema=Preferences.model_json_schema(),
                        thinking_config=types.ThinkingConfig(thinking_level="low"),
                        http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
                    ),
                ),
                timeout=min(remaining, 12),
            )
            prefs, source = Preferences.model_validate_json(response.text or ""), "gemini"
            break
        except Exception as err:  # overloaded, timed out, bad key, malformed JSON...: try the next model, then rules
            log.warning("parse_preferences failed on %s: %s", model, (str(err) or type(err).__name__)[:200])
    if prefs is None:
        prefs = heuristic_preferences(req)

    # Guardrails that don't depend on the model behaving.
    if req.clarifications and prefs.city:
        prefs.needs_clarification, prefs.clarifying_questions = False, []
    if not prefs.city:
        prefs.needs_clarification = True
        prefs.clarifying_questions = prefs.clarifying_questions[:2] or ["Which city will you be in on Saturday?"]
    prefs.clarifying_questions = prefs.clarifying_questions[:2]
    prefs.available_minutes = min(max(prefs.available_minutes, 45), 720)
    if not re.fullmatch(r"\d{1,2}:\d{2}", prefs.start_time):
        prefs.start_time = "14:00"
    return prefs, source
