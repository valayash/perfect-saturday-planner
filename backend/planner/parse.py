"""parse_preferences: structured output via AI Gateway or Gemini, with a rule-based fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time

from google import genai
from google.genai import types
from openai import AsyncOpenAI

from .fallback_data import CITY_KEYS
from .gateway import PARSE_MODELS, error_text, is_account_problem, request_options
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
    "history": r"histor|heritage|fort|palace", "movies": r"movie|film|cinema", "games": r"game|arcade",
    "bowling": r"bowling", "escape room": r"escape room", "comedy": r"comedy|stand-?up",
    "drinks": r"\bbeers?\b|\bdrinks\b|\bpubs?\b|brew|cocktail|\bwine\b|\bbars?\b",
    "dessert": r"dessert|ice cream|sweet", "street food": r"street food|chaat", "shopping": r"shop|market",
    "sea": r"\bsea\b|beach|ocean|promenade|seaside|sea ?face",
}
CONSTRAINT_WORDS = {"vegetarian": r"\bveg(etarian)?\b", "vegan": r"vegan", "jain food": r"\bjain\b", "avoid crowded places": r"crowd|quiet|calm", "no alcohol": r"no alcohol|sober|don'?t drink"}


KNOWN_CITIES = [
    "new delhi", "delhi", "gurugram", "gurgaon", "noida", "bengaluru", "bangalore", "mumbai", "bombay", "navi mumbai", "pune",
    "hyderabad", "chennai", "madras", "kolkata", "calcutta", "ahmedabad", "jaipur", "goa", "kochi", "cochin", "chandigarh",
    "lucknow", "indore", "surat", "mysuru", "mysore", "udaipur", "bhopal", "nagpur", "visakhapatnam", "coimbatore",
    "london", "new york", "paris", "singapore", "dubai", "tokyo", "sydney", "san francisco", "berlin", "amsterdam", "toronto",
]


def _known_city(text: str) -> str | None:
    """Find a well-known city name anywhere in the text, in any case ("i'm in gurgaon")."""
    low = text.lower()
    for name in sorted(KNOWN_CITIES, key=len, reverse=True):  # longest first: "new delhi" before "delhi"
        if re.search(rf"\b{re.escape(name)}\b", low):
            return name.title()
    return None


def _split_city(text: str) -> tuple[str | None, str | None]:
    """'Pune, around Koregaon Park' -> ('Pune', 'Koregaon Park')."""
    text = text.strip().strip(".")
    if not text:
        return None, None
    city, _, rest = text.partition(",")
    area = re.sub(r"^\s*(?:around|near|in|at)\s+", "", rest, flags=re.I).strip() or None
    return city.strip() or None, area


def keyword_interests(req: PlanRequest) -> list[str]:
    """Interests from the form plus keywords in the user's own words, kept specific ("bowling", not just "games")."""
    is_where = lambda q: bool(re.search(r"\bcity\b|where", q, re.I))  # noqa: E731
    blob = " ".join([req.free_text or "", *(c.answer for c in req.clarifications if not is_where(c.question))])
    interests = [i.strip().lower() for i in req.interests if i.strip()]
    interests += [k for k, rx in INTEREST_WORDS.items() if k not in interests and re.search(rx, blob, re.I)]
    if re.search(CONSTRAINT_WORDS["no alcohol"], blob, re.I) or any(re.search(r"alcohol", c, re.I) for c in req.constraints):
        interests = [i for i in interests if i != "drinks"]  # "I don't drink" is not an interest in drinks
    return interests


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
        city = _known_city(req.free_text)
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
    interests = keyword_interests(req)
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


def coerce_preferences(text: str, req: PlanRequest) -> Preferences:
    """Lay the model's JSON over the rule-based reading of the same request, so a small model that skips
    or renames a field (e.g. "available_time": "4 hours") still yields complete preferences."""
    data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    if not isinstance(data, dict):
        raise ValueError("model output is not a JSON object")
    if "available_minutes" not in data and data.get("available_time"):
        data["available_minutes"] = _minutes(str(data["available_time"]))
    base = heuristic_preferences(req).model_dump()
    merged = {**base, **{k: v for k, v in data.items() if k in Preferences.model_fields and v not in (None, "")}}
    # Small models generalise ("bowling and beer" -> "games"); keep the user's specific words alongside.
    merged["interests"] = list(dict.fromkeys([str(i).lower() for i in merged.get("interests") or []] + keyword_interests(req)))[:12]
    interests = set(merged["interests"])
    for key in ("hard_constraints", "soft_constraints"):  # small models sometimes copy interests in here
        merged[key] = [c for c in merged.get(key) or [] if str(c).lower() not in interests]
    if merged.get("city") and "needs_clarification" not in data:
        merged["needs_clarification"], merged["clarifying_questions"] = False, []
    merged["assumptions"] = [a for a in merged.get("assumptions") or [] if _real_assumption(str(a))]
    if merged.get("country_code") == "IN" and merged.get("currency") in (None, "", "local"):
        merged["currency"] = "INR"
    return Preferences.model_validate(merged)


def _real_assumption(text: str) -> bool:
    """Small models list restatements ("Budget specified as 1500") as assumptions; keep only real guesses."""
    if re.search(r"\bas (requested|specified|stated|mentioned|given|asked)\b", text, re.I):
        return False
    restates = re.search(r"\b(specified|provided|given|stated|mentioned|requested|means)\b", text, re.I)
    return not restates or bool(re.search(r"\b(not|no|wasn't|isn't|without)\b", text, re.I))


def _request_text(req: PlanRequest) -> str:
    fields = req.model_dump(exclude={"free_text", "clarifications", "run_id"}, exclude_none=True)
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


async def _parse_with_gateway(gateway: AsyncOpenAI, req: PlanRequest, deadline: float, meta: dict) -> Preferences | None:
    for model in PARSE_MODELS:
        remaining = deadline - time.monotonic()
        if remaining < 3:
            return None
        try:
            resp = await asyncio.wait_for(
                gateway.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": PARSE_SYSTEM}, {"role": "user", "content": _request_text(req)}],
                    response_format={"type": "json_schema", "json_schema": {"name": "preferences", "schema": Preferences.model_json_schema()}},
                    max_tokens=4000,
                    temperature=0,  # steadier readings: the same request no longer flips between "low" and "medium" energy
                    **request_options(model),
                ),
                timeout=min(remaining, 10),
            )
            prefs = coerce_preferences(resp.choices[0].message.content or "", req)
            meta.update(model=model, tokens=getattr(resp.usage, "total_tokens", 0) or 0)
            return prefs
        except Exception as err:
            log.warning("parse_preferences failed on gateway %s: %s", model, error_text(err))
            if is_account_problem(err):  # e.g. no card on file: no gateway model will work
                return None
    return None


async def parse_preferences(
    client: genai.Client | None, req: PlanRequest, gateway: AsyncOpenAI | None = None, meta: dict | None = None
) -> tuple[Preferences, str]:
    """Returns (preferences, source) where source is 'gateway', 'gemini' or 'rules'. Fills meta with model and tokens."""
    meta = meta if meta is not None else {}
    fallbacks = [m.strip() for m in os.getenv("GEMINI_MODELS", "gemini-3.5-flash-lite,gemini-3.8-flash,gemini-4-hydrogen").split(",") if m.strip()]
    prefs, source = None, "rules"
    deadline = time.monotonic() + PARSE_BUDGET_S  # an overloaded model should cost seconds, not a minute
    if gateway:
        prefs = await _parse_with_gateway(gateway, req, deadline, meta)
        source = "gateway" if prefs else "rules"
    models = dict.fromkeys([PARSE_MODEL, *fallbacks]) if client and not prefs else {}  # fast model first, then the chain
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
            meta.update(model=model, tokens=getattr(response.usage_metadata, "total_token_count", 0) or 0)
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
    if req.free_text and not req.city:  # chat mode: ask for missing essentials instead of assuming them
        asks = missing_details(req, prefs)
        if asks:
            prefs.needs_clarification = True
            prefs.clarifying_questions = [q["question"] for q in asks]
            meta["questions"] = asks
        elif prefs.city:
            prefs.needs_clarification, prefs.clarifying_questions = False, []
    # What the person actually stated wins over the model's reading (small models sometimes ignore "4 hours").
    stated = " ".join(x for x in [req.available_time or "", req.free_text or ""] + [c.answer for c in req.clarifications] if x)
    start, minutes = explicit_window(stated)
    if req.start_time and re.fullmatch(r"\d{1,2}:\d{2}", req.start_time):
        start = req.start_time
    if start:
        prefs.start_time = start
    if minutes:
        prefs.available_minutes = minutes
    if start or minutes:  # the user gave their times, so notes about the start, end or duration aren't assumptions
        timing = r"\b(start|starting|end|ending|until|duration|hours?|available time|time window)\b"
        prefs.assumptions = [a for a in prefs.assumptions if not re.search(timing, a, re.I)]
    if not re.fullmatch(r"\d{1,2}:\d{2}", prefs.start_time) or not (6 * 60 <= _to_min(prefs.start_time) <= 22 * 60):
        prefs.start_time = "14:00"
    room = LATEST_END - _to_min(prefs.start_time)  # plans end by 23:00, whatever the model guessed
    if prefs.available_minutes > room:
        prefs.assumptions.append(f"Plans end by 23:00, so this one runs {prefs.start_time}–23:00 ({room // 60} h {room % 60:02d} min).")
    prefs.available_minutes = max(45, min(prefs.available_minutes, room, 720))
    return prefs, source


LATEST_END = 23 * 60

_CLOCK = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"


def _clock(h: str, m: str | None, ampm: str | None, default_pm: bool) -> int:
    hour, minute = int(h) % 24, int(m or 0)
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    elif ampm is None and default_pm and 1 <= hour <= 9:
        hour += 12  # "from 4" on a Saturday outing means 4 pm
    return hour * 60 + minute


def explicit_window(text: str) -> tuple[str | None, int | None]:
    """(start "HH:MM", minutes) the person stated explicitly, if any: "5–10 pm", "4 hours from 3pm", "half day"."""
    t = text.lower()
    rng = re.search(rf"\b{_CLOCK}\s*(?:-|–|—|to|till|until)\s*{_CLOCK}\b", t)
    if rng and (rng.group(3) or rng.group(6)):
        end_ampm = rng.group(6)
        start_ampm = rng.group(3) or (end_ampm if end_ampm and int(rng.group(1)) <= int(rng.group(4)) else None)
        a = _clock(rng.group(1), rng.group(2), start_ampm, default_pm=True)
        b = _clock(rng.group(4), rng.group(5), end_ampm, default_pm=True)
        if b <= a:
            b += 12 * 60 if b + 12 * 60 > a else 24 * 60
        if 30 <= b - a <= 14 * 60:
            return f"{a // 60:02d}:{a % 60:02d}", b - a
    minutes = None
    hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b", t)
    mins = re.search(r"(\d+)\s*(?:m|min|mins|minutes)\b", t)
    if hours or mins:
        minutes = int((float(hours.group(1)) * 60 if hours else 0) + (int(mins.group(1)) if mins else 0)) or None
    elif re.search(r"half[- ]day|half a day", t):
        minutes = 240
    elif re.search(r"full[- ]day|whole day|all day", t):
        minutes = 600
    start = end = None
    at = re.search(r"\b(?:from|at|after|starting|start at)\s+" + _CLOCK + r"\b", t)
    until = re.search(r"\b(?:until|till|til|to|by|before)\s+" + _CLOCK + r"\b", t)
    if not at:  # a bare "3pm" that isn't an end time ("until 10 pm")
        for bare in re.finditer(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t):
            if not until or bare.start() != until.start(1):
                at = bare
                break
    if at:
        m = _clock(at.group(1), at.group(2), at.group(3), default_pm=True)
        if 6 * 60 <= m <= 22 * 60:
            start = m
    if until and (until.group(3) or start is not None):
        end = _clock(until.group(1), until.group(2), until.group(3), default_pm=True)
    if start is not None and end is not None and minutes is None and 30 <= end - start <= 14 * 60:
        minutes = end - start
    return (f"{start // 60:02d}:{start % 60:02d}" if start is not None else None), minutes

# ---------- chat mode: what's missing? (code, not model judgement, so nothing is silently assumed) ----------

CITY_ALIASES = {"bengaluru": ["bangalore", "bengaluru", "blr"], "mumbai": ["mumbai", "bombay"], "delhi": ["delhi", "new delhi"],
                "gurugram": ["gurugram", "gurgaon"], "kolkata": ["kolkata", "calcutta"], "chennai": ["chennai", "madras"]}
MONEY = re.compile(r"₹|\brs\.?\s*\d|\binr\b|rupee|\$|£|€|\busd\b|\beur\b|\bgbp\b|bucks|budget|\b\d+\s*k\b|"
                   r"\b\d{3,6}\b(?!\s*(?:hrs?|hours?|mins?|minutes?|am|pm|:|-|to\b))|free|no limit|cheap", re.I)
DURATION = re.compile(r"\b\d+(?:\.\d+)?\s*(?:h|hr|hrs|hour|hours|mins?|minutes?)\b|half[- ]day|full[- ]day|whole day|all day|"
                      r"\b(?:till|until|to|-|–)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b.*|\bafternoon\b|\bevening\b|\bmorning\b|\bnight\b", re.I)
START = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b|\b\d{1,2}:\d{2}\b|\bnoon\b|after lunch|morning|afternoon|evening|night", re.I)
MOOD_OR_INTEREST = re.compile(
    r"tired|exhaust|lazy|chill|relax|calm|cozy|cosy|happy|bored|sad|stressed|energ|excit|adventur|romantic|social|curious|"
    r"food|eat|cafe|café|coffee|music|gig|walk|park|nature|art|museum|book|read|history|movie|film|game|bowling|arcade|"
    r"shop|beer|drink|pub|bar\b|dessert|street food|theatre|comedy|sport|lake|garden|photograph|culture", re.I)


def _answered(req: PlanRequest) -> dict[str, str]:
    """Which details earlier clarifying answers covered (keyed by our question ids)."""
    got = {}
    for c in req.clarifications:
        q = c.question.lower()
        key = "city" if "city" in q else "budget" if "budget" in q else "time" if ("time" in q or "how long" in q) else "mood" if "mood" in q else None
        if key:
            got[key] = c.answer.strip()
    return got


def missing_details(req: PlanRequest, prefs: Preferences) -> list[dict]:
    text = " ".join([req.free_text or ""] + [c.answer for c in req.clarifications])
    low = text.lower()
    got = _answered(req)
    asks = []

    city = (prefs.city or "").strip().lower()
    names = next((v for v in CITY_ALIASES.values() if city in v or any(a == city for a in v)), [city] if city else [])
    if "city" not in got and not (city and any(n and n in low for n in names)):
        asks.append({"id": "city", "question": "Which city would you like to plan for?", "options": ["Bangalore", "Mumbai", "Delhi"]})

    india = (prefs.country_code or "IN") == "IN" or not prefs.city
    if "budget" not in got and not MONEY.search(text):
        opts = ["₹500", "₹1500", "₹3000"] if india else ["25", "60", "120"]
        asks.append({"id": "budget", "question": "What's your budget per person for the day?", "options": opts})

    if "time" not in got and not DURATION.search(text):
        start = START.search(text)
        if start:
            s = start.group(0)
            asks.append({"id": "time", "question": f"How much time do you have from {s}?", "options": ["2 hours", "4 hours", "Until 10 pm"]})
        else:
            asks.append({"id": "time", "question": "How much time do you have, and from when?",
                         "options": ["4 hours from 3 pm", "Afternoon, 12–6 pm", "Evening, 5–10 pm", "Whole day, 10 am–8 pm"]})

    if "mood" not in got and not MOOD_OR_INTEREST.search(text):
        asks.append({"id": "mood", "question": "What are you in the mood for?",
                     "options": ["Chill: coffee, books, walks", "Foodie: cafés and street food", "Culture: art, museums, music", "Active: games, sports, exploring"]})
    return asks


def _to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)
