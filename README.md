# ☀️ Perfect Saturday Planner

An AI agent that plans a fun, realistic Saturday from your city, budget, time, mood, interests and constraints. It checks the real forecast, finds real places near you, checks the plan is actually doable, explains why each stop fits you, and shows a live trace of everything it did.

**Live URL:** https://perfect-saturday-planner-nine.vercel.app · **System design:** [docs/SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md)

## How it works

```
Browser (Next.js) ──POST /api/plan──▶ FastAPI (Python) ──▶ LLM tool loop
                                                           (Vercel AI Gateway → Gemini API → rules)
        ◀──────── Server-Sent Events: trace steps, thinking, final plan ────────
```

1. **`parse_preferences`**: an LLM (structured JSON output) turns the form or free text into typed preferences: minutes, energy level, hard vs soft constraints. In chat ("Just describe it") mode it never assumes the essentials: if the city, budget, time (how long and from when) or mood is missing, code detects it and the agent asks for exactly those, each with quick-pick suggestions. Plans always end by 23:00.
2. **`resolve_location`**: geocodes the city and neighbourhood (Open-Meteo).
3. **Gather (code, in parallel)**: Saturday's forecast plus activity and food candidates near the user's area: well-known curated places first (with hours, prices and veg status), topped up with live OpenStreetMap results.
4. **Plan + revise (LLM agent)**: the model gets the candidates and calls `submit_plan`. The server fixes timing slips itself (moves stops later to fit real travel time, rounds to 5 min), validates the rest and sends back anything the model must fix. Typically 2 LLM calls in total (~6K tokens, ~10–20 s), with `gpt-oss` served by Cerebras/Groq through Vercel AI Gateway.

Plans include the ride from the user's area to the first stop and back home ("home around 19:04"), a 5% budget buffer, crowd level at that hour, the rain window, a plain-language "Worth knowing" list, the parser's assumptions and a Google Maps route.

Tools:

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

- **Places:** OpenStreetMap through two free, keyless APIs queried in parallel: **Overpass** (rich tags: opening hours, `diet:vegetarian`, cuisine) and **Photon** (fast OSM search). The public Overpass servers are often at capacity, so Photon usually answers; Overpass wins when it responds within 1.5 s.
- **Weather + geocoding:** Open-Meteo.
- **Prices and crowd levels** aren't in OSM, so they're category heuristics (tuned for India, generic elsewhere) and labelled "est." everywhere.

## Failure handling

| Situation | What happens |
|---|---|
| Missing details in chat mode (city, budget, time, mood) | One question card listing exactly what's missing, with quick picks; each detail is asked once, then it plans |
| Unknown city | Asks which city you mean |
| Overpass overloaded | Photon answers instead (trace shows the source) |
| Both live sources down, or no matches | Widens the radius once, then curated places (Bengaluru, Mumbai, Delhi, Gurugram), then honest generic suggestions |
| Rain forecast | Indoor picks; plan B always included |
| Plan breaks rules | Validator errors go back to the model to fix; warnings must be explained as trade-offs |
| A model is overloaded (503) or rate-limited (429) | Next model in the chain: AI Gateway models first (`gpt-oss-120b` → `gemini-2.5-flash-lite` → `qwen3.7-flash`, all on the free tier), then the Gemini API models; the work so far is handed over |
| Gemini unavailable (no key, outage) | Rule-based parse + rule-based plan from the same tools, clearly labelled |
| Weather API down | Plans for uncertainty, mixes indoor and outdoor |
| Live stream stalls in transit | The browser switches to long-polling the stored run (`GET /api/plan/{run_id}?after=N`), or re-runs once without streaming |

## Run locally

Prereqs: Node 18+, Python 3.12+, [uv](https://docs.astral.sh/uv/).

```bash
# backend (http://localhost:8000)
cd backend
cp .env.example .env        # then add AI_GATEWAY_API_KEY and/or GEMINI_API_KEY
uv run uvicorn main:app --reload --port 8000
```

```bash
# frontend (http://localhost:3000), proxies /api to the backend in dev
cd frontend
npm install
npm run dev
```

LLM providers (any one is enough; the chain uses whatever is configured): `AI_GATEWAY_API_KEY` (local only; on Vercel the project's OIDC token is used automatically) and/or `GEMINI_API_KEY`.

Optional env vars (all read in `backend/planner/llm.py`): `AI_GATEWAY_MODELS` (default `openai/gpt-oss-120b,google/gemini-2.5-flash-lite,alibaba/qwen3.7-flash`), `AI_GATEWAY_PARSE_MODELS` (default `openai/gpt-oss-20b,openai/gpt-oss-120b,google/gemini-2.5-flash-lite`), `AI_GATEWAY_FAST_PROVIDERS` (default `cerebras,groq`), `GEMINI_MODELS` (comma-separated fallback chain, default `gemini-3.5-flash-lite,gemini-3.8-flash,gemini-4-hydrogen`) and `GEMINI_PARSE_MODEL` (default `gemini-3.5-flash-lite`).

## Deploy (Vercel)

One Vercel project with two [Services](https://vercel.com/docs/services), defined in `vercel.json`: the Next.js frontend at `/` and the FastAPI backend at `/api/*`. Python functions stream on Vercel, so the trace is live in production too.

```bash
vercel link
vercel env add GEMINI_API_KEY production   # optional backup; AI Gateway needs no key on Vercel (OIDC)
vercel deploy --prod
```

## Project layout

```
vercel.json              two services: web (Next.js) + api (FastAPI)
frontend/app/            Next.js UI: form, live trace, plan view
backend/main.py          FastAPI app: SSE endpoint, keep-alives, simple rate limit
backend/planner/
  agent.py               provider chain: AI Gateway loop → Gemini loop → rule-based plan
  llm.py                 AI Gateway (OpenAI-compatible, OIDC or key auth) and Gemini clients, model chains
  parse.py               parse_preferences (structured output + rules fallback)
  tools.py               tool schemas, execution, final plan assembly
  places.py              Overpass + Photon + curated/generic fallbacks
  checks.py              estimate_cost, validate_plan
  geo.py, weather.py, hours.py, pricing.py, fallback_data.py
```

## How I used AI tools

_Draft, edit to taste:_ I built this with Claude Code. I used it to plan the architecture, scaffold the Next.js + FastAPI app, write the agent loop and tools, and test each failure path in a browser. I made the key calls: Python over Go after checking Vercel's streaming support, a provider chain (Vercel AI Gateway first, then Gemini) after Gemini's free tier kept returning 503s, and adding Photon after finding the public Overpass servers overloaded during testing.
