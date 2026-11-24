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
3. **Gather (code, in parallel)**: Saturday's forecast plus activity and food candidates near the user's area from a live places API (Geoapify, or the public OpenStreetMap servers); curated places only fill gaps and add known prices, hours and veg status to matching live results.
4. **Plan + revise (LLM agent)**: the model gets the candidates and calls `submit_plan`. The server fixes timing slips itself (moves stops later to fit real travel time, rounds to 5 min), validates the rest and sends back anything the model must fix. Typically 2–3 LLM calls in total (~7–15K tokens; median ~7 s in the evals): parsing on Gemini 2.5 Flash-Lite and planning on `gpt-oss-120b` (Cerebras/Groq), both through Vercel AI Gateway.

Plans include the ride from the user's area to the first stop and back home ("home around 19:04"), a 5% budget buffer, crowd level at that hour, the rain window, a plain-language "Worth knowing" list, the parser's assumptions, a **route map** with numbered stops (Geoapify Static Maps, served through a signed `/api/map` URL so the key stays on the server) and a Google Maps route.

Tools:

| Tool | What it does | Data |
|---|---|---|
| `get_weather` | Saturday's hourly rain chance, temperature, sunset | Open-Meteo (live) |
| `find_activities` | Parks, lakes, museums, galleries, live music, bookstores… near you | Geoapify / OpenStreetMap (live) → curated fill → generic |
| `find_food` | Cafés and restaurants that respect diet constraints | Geoapify / OpenStreetMap (live) → curated fill → generic |
| `estimate_cost` | Tickets + food + travel legs (walk or auto/cab) vs budget | deterministic |
| `validate_plan` | Time window, travel time between stops, budget, opening hours, veg, crowds, rain vs outdoor, sunset, distance | deterministic |
| `submit_plan` | Final plan, re-validated server-side; rejected (up to 2×) if it still has errors | — |

The model can only use places the search tools returned (unknown `place_id`s are rejected; an ID that differs only in the OSM type letter, or the place's exact name, is matched to the place it means), so it can't make up venues. A plan that uses the same place twice is rejected too.

## Real data, scrappily

- **Places:** live from **Geoapify Places** (OpenStreetMap data with opening hours, `diet:vegetarian` and cuisine tags; free key, no card) when `GEOAPIFY_API_KEY` is set. Without it, or if it fails, two free keyless OSM APIs race each other: **Overpass** (rich tags, often at capacity) and **Photon** (fast search). Neighbourhoods are geocoded through Geoapify too. Curated places (Bengaluru, Mumbai, Delhi, Gurugram) only fill thin results.
- **Weather + geocoding:** Open-Meteo.
- **Prices and crowd levels** aren't in OSM, so they're category heuristics (tuned for India, generic elsewhere; names like "canteen", "Udupi" or "dhaba" get a budget-eatery estimate) and labelled "est." everywhere.
- **Opening hours** come from OpenStreetMap tags when a place has them. Geoapify's Place Details endpoint was tested as a second source and added nothing (0 of 24 places), so plans say "check hours" when OSM doesn't know.

## Failure handling

| Situation | What happens |
|---|---|
| Missing details in chat mode (city, budget, time, mood) | One question card listing exactly what's missing, with quick picks; each detail is asked once, then it plans |
| Unknown city | Asks which city you mean |
| Geoapify missing or down | Overpass and Photon race instead (trace shows the source) |
| Overpass overloaded | Photon answers instead |
| Live results thin, or all live sources down | Widens the radius once, then curated places (Bengaluru, Mumbai, Delhi, Gurugram), then honest generic suggestions |
| Rain forecast | Indoor picks; plan B always included |
| Plan breaks rules | Validator errors go back to the model to fix; warnings must be explained as trade-offs |
| A model is overloaded (503) or rate-limited (429) | Next model in the chain: AI Gateway models first (`gpt-oss-120b` → `gemini-2.5-flash-lite` → `qwen3.7-flash`, all on the free tier), then the Gemini API models; the work so far is handed over |
| Gemini unavailable (no key, outage) | Rule-based parse + rule-based plan from the same tools, clearly labelled |
| Weather API down | Plans for uncertainty, mixes indoor and outdoor |
| Live stream stalls in transit | The browser switches to long-polling the stored run (`GET /api/plan/{run_id}?after=N`), or re-runs once without streaming |

Two failure drills are built in, for the evals and for anyone testing the API: add `"simulate": ["rain"]` to a request to force rain through the whole window, or `"simulate": ["llm_down"]` to run with every LLM unavailable (you get the rule-based plan).

## Evals

`python3 evals/run_evals.py` sends 10 fixed requests to the live API (or `--base http://localhost:8000`) and checks each result automatically: the plan passes the server's validation, has 2–5 explained and distinct stops, stays inside the budget and time window, has a route map, and meets the scenario's own expectations (vegetarian food only, indoor in rain, a free stop on a tight budget, asking before planning a vague request, and so on). It writes [`evals/RESULTS.md`](evals/RESULTS.md).

Latest run against the live site, every scenario twice (2026-11-24 18:55 UTC):

**8/10 scenarios passed every check in every run (18/20 runs clean).** Plans took 0.1–16.5 s (median 7.2 s); agent plans used 2.6 LLM calls on average.

| Scenario | Result | Checks | Stops (first passing run) | Time | LLM calls | Tokens |
|---|---|---|---|---|---|---|
| The assignment's example input | ⚠️ 1/2 runs | 11/11 | Venkatappa Art Gallery → Chowdiah Memorial Hall → Konark Kanteerava | 6.9 s | 3 | 15456 |
| Vague chat request | ✅ 2/2 runs | 2/2 | asked: city, budget, time, mood | 2.0 s | 0 | 0 |
| Vague request + answers (Pune) | ✅ 2/2 runs | 10/10 | 7 to 9 Grams Coffee → Osho Teerth Park → Blue Tokai Coffee Roasters | 7.5 s | 2 | 7141 |
| Rain all afternoon (simulated) | ✅ 2/2 runs | 8/8 | Ainmane → Manora → Artisera | 8.2 s | 2 | 6719 |
| Smaller city with thin map data (Nashik) | ✅ 2/2 runs | 8/8 | Baba Ambedkar Municipal Gardens → Kulkarni's Pav Bhaji → WeGo Art Gallery and Art Exhibition | 9.7 s | 3 | 12531 |
| Tight budget (₹300 in Mumbai) | ⚠️ 1/2 runs | 8/8 | Colaba Woods Garden → Mumbai Port Trust Garden → art gallery → Oxford Bookstore | 14.7 s | 4 | 23797 |
| Wish only available far away (bowling, Gurugram) | ✅ 2/2 runs | 9/9 | The Big Shot → Om Bookshop → Barista | 8.0 s | 2.5 | 12496 |
| History, art and a North Indian lunch (Delhi) | ✅ 2/2 runs | 9/9 | Lokayata Art Gallery → Maheshi Da Frontier Punjabi Dhaba → OPS → Barah Khamba → Kunzum | 8.2 s | 2 | 7742 |
| Outside India (London) | ✅ 2/2 runs | 9/9 | Nero Express → National Gallery → Notes | 4.8 s | 2 | 7650 |
| Every LLM unavailable (simulated) | ✅ 2/2 runs | 9/9 | St. John's Auditorium → Utsav | 1.0 s | 0 | 0 |

Both misses are plans where the model's two allowed revisions didn't clear the last error (a gallery visit running past closing time; ₹370 against a ₹300 budget). The server then shows the problem in the plan's heads-up box instead of hiding it. Building the set paid off: it caught the model skipping a requested interest (now listed for it up front), budget plans that never walked anywhere (now free up to ~1 km, with budget-eatery prices), and a stricter rule that made plans slower and worse (rolled back to a single nudge).

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

Places (optional, recommended): `GEOAPIFY_API_KEY`, a free key from [myprojects.geoapify.com](https://myprojects.geoapify.com) (no card, 3,000 credits/day; a plan uses about 10). Without it the planner uses the public OpenStreetMap servers.

Optional env vars: `AI_GATEWAY_MODELS` (default `openai/gpt-oss-120b,google/gemini-2.5-flash-lite,alibaba/qwen3.7-flash`), `AI_GATEWAY_PARSE_MODELS` (default `google/gemini-2.5-flash-lite,openai/gpt-oss-120b,openai/gpt-oss-20b`), `GEMINI_MODELS` (comma-separated fallback chain, default `gemini-3.5-flash-lite,gemini-3.8-flash,gemini-4-hydrogen`) and `GEMINI_PARSE_MODEL` (default `gemini-3.5-flash-lite`).

## Deploy (Vercel)

One Vercel project with two [Services](https://vercel.com/docs/services), defined in `vercel.json`: the Next.js frontend at `/` and the FastAPI backend at `/api/*`. Python functions stream on Vercel, so the trace is live in production too.

```bash
vercel link
vercel env add GEMINI_API_KEY production   # optional backup; AI Gateway needs no key on Vercel (OIDC)
vercel env add GEOAPIFY_API_KEY production # optional; live places via Geoapify
vercel deploy --prod
```

## Project layout

```
vercel.json              two services: web (Next.js) + api (FastAPI)
frontend/app/            Next.js UI: form, live trace, plan view
backend/main.py          FastAPI app: SSE endpoint, keep-alives, simple rate limit, route map endpoint
backend/planner/
  agent.py               provider chain: AI Gateway loop → Gemini loop → rule-based plan
  gateway.py             Vercel AI Gateway client (OpenAI-compatible, OIDC or key auth)
  parse.py               parse_preferences (structured output + rules fallback)
  tools.py               tool schemas, execution, final plan assembly
  places.py              Geoapify + Overpass + Photon, curated/generic fallbacks
  checks.py              estimate_cost, validate_plan
  maps.py                route map images behind signed /api/map URLs
  geo.py, weather.py, hours.py, pricing.py, fallback_data.py
evals/run_evals.py       10 fixed scenarios with automatic checks (see Evals)
```

## How I used AI tools

_Draft, edit to taste:_ I built this with Claude Code. I used it to plan the architecture, scaffold the Next.js + FastAPI app, write the agent loop and tools, and test each failure path in a browser. I made the key calls: Python over Go after checking Vercel's streaming support, a provider chain (Vercel AI Gateway first, then Gemini) after Gemini's free tier kept returning 503s, and adding Photon after finding the public Overpass servers overloaded during testing.
