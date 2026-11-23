# ☀️ Perfect Saturday Planner

An AI agent that plans a fun, realistic Saturday from your city, budget, time, mood, interests and constraints. It checks the real forecast, finds real places near you, checks the plan is actually doable, explains why each stop fits you, and shows a live trace of everything it did.

**Live URL:** https://perfect-saturday-planner-nine.vercel.app · **System design:** [docs/SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md)

## How it works

```
Browser (Next.js) ──POST /api/plan──▶ FastAPI (Python) ──▶ Gemini function-calling loop
        ◀──────── Server-Sent Events: trace steps, thinking, final plan ────────
```

1. **`parse_preferences`**: Gemini (structured JSON output) turns the form or free text into typed preferences: minutes, energy level, hard vs soft constraints. If the input is too vague (no city, "plan something fun"), it asks 1–2 clarifying questions instead of guessing.
2. **`resolve_location`**: geocodes the city and neighbourhood (Open-Meteo).
3. **Agent loop**: Gemini decides which tools to call (usually weather + activities + food in parallel), drafts a plan, validates it, fixes errors and submits:

| Tool | What it does | Data |
|---|---|---|
| `get_weather` | Saturday's hourly rain chance, temperature, sunset | Open-Meteo (live) |
| `find_activities` | Parks, lakes, museums, galleries, live music, bookstores… near you | OpenStreetMap (live) → curated → generic |
| `find_food` | Cafés and restaurants that respect diet constraints | OpenStreetMap (live) → curated → generic |
| `estimate_cost` | Tickets + food + travel legs (walk or auto/cab) vs budget | deterministic |
| `validate_plan` | Time window, travel time between stops, budget, opening hours, veg, crowds, rain vs outdoor, sunset, distance | deterministic |
| `submit_plan` | Final plan, re-validated server-side; rejected (up to 2×) if it still has errors | — |

The model can only use places the search tools returned (unknown `place_id`s are rejected), so it can't make up venues.

## Real data, scrappily

- **Places:** OpenStreetMap through two free, keyless APIs queried in parallel: **Overpass** (rich tags: opening hours, `diet:vegetarian`, cuisine) and **Photon** (fast OSM search). The public Overpass servers are often at capacity, so Photon usually answers; Overpass wins when it responds within 5s.
- **Weather + geocoding:** Open-Meteo.
- **Prices and crowd levels** aren't in OSM, so they're category heuristics (tuned for India, generic elsewhere) and labelled "est." everywhere.

## Failure handling

| Situation | What happens |
|---|---|
| Vague input / no city | 1–2 clarifying questions, answered inline, then it continues |
| Unknown city | Asks which city you mean |
| Overpass overloaded | Photon answers instead (trace shows the source) |
| Both live sources down, or no matches | Widens the radius once, then curated places (Bengaluru, Mumbai, Delhi), then honest generic suggestions |
| Rain forecast | Indoor picks; plan B always included |
| Plan breaks rules | Validator errors go back to the model to fix; warnings must be explained as trade-offs |
| Gemini model overloaded (503) or rate-limited (429) | Switches to the next model in `GEMINI_MODELS` before the first turn; retries with backoff mid-run |
| Gemini unavailable (no key, outage) | Rule-based parse + rule-based plan from the same tools, clearly labelled |
| Weather API down | Plans for uncertainty, mixes indoor and outdoor |

## Run locally

Prereqs: Node 18+, Python 3.12+, [uv](https://docs.astral.sh/uv/).

```bash
# backend (http://localhost:8000)
cd backend
cp .env.example .env        # then put your GEMINI_API_KEY in .env
uv run uvicorn main:app --reload --port 8000
```

```bash
# frontend (http://localhost:3000), proxies /api to the backend in dev
cd frontend
npm install
npm run dev
```

Optional env vars: `GEMINI_MODELS` (comma-separated fallback chain, default `gemini-3.5-flash-lite,gemini-4-hydrogen,gemini-3.5-flash`) and `GEMINI_PARSE_MODEL` (default `gemini-3.5-flash-lite`).

## Deploy (Vercel)

One Vercel project with two [Services](https://vercel.com/docs/services), defined in `vercel.json`: the Next.js frontend at `/` and the FastAPI backend at `/api/*`. Python functions stream on Vercel, so the trace is live in production too.

```bash
vercel link
vercel env add GEMINI_API_KEY production
vercel deploy --prod
```

## Project layout

```
vercel.json              two services: web (Next.js) + api (FastAPI)
frontend/app/            Next.js UI: form, live trace, plan view
backend/main.py          FastAPI app: SSE endpoint, keep-alives, simple rate limit
backend/planner/
  agent.py               Gemini function-calling loop, model fallback chain, rule-based fallback
  parse.py               parse_preferences (structured output + rules fallback)
  tools.py               tool schemas, execution, final plan assembly
  places.py              Overpass + Photon + curated/generic fallbacks
  checks.py              estimate_cost, validate_plan
  geo.py, weather.py, hours.py, pricing.py, fallback_data.py
```

## How I used AI tools

_Draft, edit to taste:_ I built this with Claude Code. I used it to plan the architecture, scaffold the Next.js + FastAPI app, write the agent loop and tools, and test each failure path in a browser. I made the key calls: Python over Go after checking Vercel's streaming support, Gemini as the model with a fallback chain because the bigger models were often at capacity, and adding Photon after finding the public Overpass servers overloaded during testing.
