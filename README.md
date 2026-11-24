# ☀️ Perfect Saturday Planner

An AI agent that plans a fun, realistic Saturday from your city, budget, time, mood, interests and constraints. It checks the real forecast, finds real places near you, makes sure the plan is actually doable, explains why each stop fits you, and shows a live trace of everything it did.

**Live:** https://perfect-saturday-planner-nine.vercel.app · **Design doc:** [docs/SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md) · **Eval results:** [evals/RESULTS.md](evals/RESULTS.md)

## Try it

- Pick an example (the first one is the assignment's input) or fill in the form, then press **Plan my Saturday**. The trace shows each step as it happens.
- Switch to **Just describe it** and type something vague like "plan my saturday": the agent asks for what's missing (city, budget, time, mood) before it plans.
- Failure drills through the API: `"simulate": ["rain"]` forces rain all afternoon, `"simulate": ["llm_down"]` switches every model off so you get the rule-based plan:

```bash
curl -s -X POST 'https://perfect-saturday-planner-nine.vercel.app/api/plan?mode=json' \
  -H 'Content-Type: application/json' \
  -d '{"city":"Bengaluru","budget":1500,"available_time":"4 hours from 2pm","interests":["walks","coffee"],"simulate":["rain"]}'
```

## How it works

```
Browser (Next.js) ──POST /api/plan──▶ FastAPI (Python) ──▶ agent
        ◀──── Server-Sent Events: trace steps, model reasoning, final plan ────
```

1. **Understand** (LLM): structured JSON output turns the form or free text into typed preferences: time window, energy level, hard vs soft constraints. In chat mode, missing essentials become clarifying questions with quick picks instead of guesses.
2. **Locate** (code): geocodes the city and neighbourhood.
3. **Gather** (code, in parallel): Saturday's forecast plus activity and food candidates from live map data, including the nearest match for anything you asked for by name (e.g. the closest bowling alley).
4. **Plan and revise** (LLM agent with tools): the model plans from the candidates and calls `submit_plan`. The server repairs timing slips itself, validates everything else and sends back what must change. Typically 2–3 LLM calls and about 7 s.

Why this shape: when the model decided every lookup, a plan took 4–6 LLM calls and 30–40 s. Gathering in code and letting the model plan and fix cut that to about 2 calls. A deterministic validator keeps the plan honest: only places the tools returned, inside your time window and budget, open when you get there.

Each plan has travel legs from your area and back home, costs with a 5% buffer, crowd levels at that hour, the rain window, trade-offs, a plan B, a route map with numbered stops and a Google Maps link.

| Tool | What it does | Data |
|---|---|---|
| `get_weather` | Saturday's hourly rain chance, temperature and sunset | Open-Meteo |
| `find_activities` | Parks, museums, galleries, bookshops, bowling, beaches… near you | Geoapify / OpenStreetMap, curated fill |
| `find_food` | Cafés, restaurants and bars that respect diet constraints, favouring a cuisine you asked for | Geoapify / OpenStreetMap, curated fill |
| `estimate_cost` | Tickets, food and every ride (walk or auto/cab) against the budget | deterministic |
| `validate_plan` | Time window, travel between stops, budget, opening hours, diet, crowds, rain, sunset, distance | deterministic |
| `submit_plan` | The final plan, re-validated by the server; sent back (up to twice) if it still has errors | deterministic |

## Real data

- **Places:** [Geoapify Places](https://www.geoapify.com/places-api) (OpenStreetMap data with opening hours, diet and cuisine tags; free key, no card). Without a key, [Photon](https://photon.komoot.io) (keyless OpenStreetMap search). Curated well-known places (Bengaluru, Mumbai, Delhi, Gurugram) only fill gaps and add known prices to matching live places.
- **Weather and geocoding:** Open-Meteo, plus Geoapify for neighbourhoods.
- **Route map:** Geoapify Static Maps, fetched by the backend through a signed `/api/map` URL so the key never reaches the browser.
- **Estimates:** OpenStreetMap has no prices or crowd levels, so those are heuristics and labelled "est.". Opening hours come from OpenStreetMap when a place has them; otherwise the plan says to check.

## Failure handling

| Situation | What happens |
|---|---|
| Vague request in chat mode | One question card for exactly what's missing, then it plans |
| Unknown city | Asks which city you mean |
| A model is busy, rate-limited or failing | One retry, then the next AI Gateway model (`gpt-oss-120b` → `gemini-2.5-flash-lite` → `qwen3.7-flash`) with the same history |
| Every model unavailable | Rule-based parse and plan from the same tools and validator, clearly labelled |
| Plan breaks a rule | The validator's errors go back to the model; warnings must be explained as trade-offs |
| Something you asked for isn't nearby | The nearest one within 25 km is offered with food around it, or a trade-off explains why it's left out |
| Rain forecast | Indoor picks and a plan B |
| Geoapify missing or down | Photon instead; thin results widen the radius, then curated places, then honest generic suggestions |
| Weather API down | Plans for uncertainty and mixes indoor and outdoor |
| Live stream stalls in transit | The browser long-polls the stored run (`GET /api/plan/{run_id}`), or re-runs once without streaming |

## Evals

`python3 evals/run_evals.py` sends 10 fixed requests to the live API (or `--base http://localhost:8000`) and checks each result automatically: the plan passes the server's validation, has 2–5 explained and distinct stops, stays inside the budget and time window, has a route map, and meets the scenario's own expectation (vegetarian food only, indoor in rain, a free stop on a tight budget, asking before planning a vague request, and so on). `--repeat N` runs each scenario N times, because LLM output varies.

<!-- results -->
Latest run against the live site, every scenario twice:

**7/10 scenarios passed every check in every run (17/20 runs clean).** Plans took 0.1–21.8 s (median 7.7 s); agent plans used 2.4 LLM calls on average.

| Scenario | Result | Checks | Stops (first passing run) | Time | LLM calls | Tokens |
|---|---|---|---|---|---|---|
| The assignment's example input | ⚠️ 1/2 runs | 11/11 | Cubbon Park → Konark Kanteerava → Chowdiah Memorial Hall | 5.1 s | 2 | 7861 |
| Vague chat request | ✅ 2/2 runs | 2/2 | asked: city, budget, time, mood | 2.0 s | 0 | 0 |
| Vague request + answers (Pune) | ✅ 2/2 runs | 10/10 | 7 to 9 Grams Coffee → Osho Teerth Park → Shahu Modak Udyan | 4.7 s | 2 | 7330 |
| Rain all afternoon (simulated) | ✅ 2/2 runs | 8/8 | Ainmane → Manora → Artisera → Champaca | 7.4 s | 2.5 | 10883 |
| Smaller city with thin map data (Nashik) | ✅ 2/2 runs | 8/8 | Baba Ambedkar Municipal Gardens → Kulkarni's Pav Bhaji → WeGo Art Gallery and Art Exhibition | 9.8 s | 2 | 6436 |
| Tight budget (₹300 in Mumbai) | ⚠️ 1/2 runs | 8/8 | Colaba Woods Garden → art gallery → Kitab Khana | 14.9 s | 3.5 | 19526 |
| Wish only available far away (bowling, Gurugram) | ⚠️ 1/2 runs | 9/9 | The Big Shot → The Laugh Store | 12.2 s | 3.5 | 21314 |
| History, art and a North Indian lunch (Delhi) | ✅ 2/2 runs | 9/9 | Barah Khamba → Lokayata Art Gallery → Maheshi Da Frontier Punjabi Dhaba → OPS → Kunzum | 11.7 s | 2 | 7498 |
| Outside India (London) | ✅ 2/2 runs | 9/9 | National Gallery → Notes → National Portrait Gallery | 7.1 s | 2 | 6974 |
| Every LLM unavailable (simulated) | ✅ 2/2 runs | 9/9 | St. John's Auditorium → Utsav | 1.0 s | 0 | 0 |

The misses change from run to run because LLM output does. This time: a crowded park picked despite "avoid crowds" without a trade-off, a budget plan still at ₹369 against ₹300 after its two revisions, and bowling left out without saying why. The first two still reach the user as heads-up warnings on the plan; the third is a gap the evals exposed.
<!-- /results -->

The set earned its keep: it caught the model skipping an interest the user named (the matching candidates are now listed for it up front), budget plans that never walked anywhere (walks up to ~1 km are now free, and budget eateries get cheaper estimates), and a stricter rule that made plans slower and worse (rolled back to a single nudge).

## Run locally

Prerequisites: Node 18+, Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
cd backend
cp .env.example .env    # add AI_GATEWAY_API_KEY; GEOAPIFY_API_KEY is optional but recommended
uv run uvicorn main:app --reload --port 8000
```

```bash
cd frontend
npm install
npm run dev             # http://localhost:3000, proxies /api to the backend
```

- `AI_GATEWAY_API_KEY`: a [Vercel AI Gateway](https://vercel.com/ai-gateway) key. On Vercel the project's OIDC token is used instead.
- `GEOAPIFY_API_KEY`: free from [myprojects.geoapify.com](https://myprojects.geoapify.com) (no card, 3,000 credits a day; a plan uses about 10–25). Without it, places come from Photon.
- Optional: `AI_GATEWAY_MODELS` and `AI_GATEWAY_PARSE_MODELS` override the model chains; `PLAN_RATE_LIMIT` sets plans per IP per 10 minutes (default 20).

## Deploy (Vercel)

One Vercel project with two [Services](https://vercel.com/docs/services), defined in `vercel.json`: the Next.js frontend at `/` and the FastAPI backend at `/api/*`. Python functions stream on Vercel, so the trace is live in production too.

```bash
vercel link
vercel env add GEOAPIFY_API_KEY production
vercel deploy --prod
```

## Project layout

```
vercel.json              two services: web (Next.js) + api (FastAPI)
frontend/app/            UI: form or free text, live trace, plan view with route map
backend/main.py          API: /api/plan (SSE or JSON), /api/plan/{run_id}, /api/map, rate limit
backend/planner/
  agent.py               the workflow, the AI Gateway tool loop and the rule-based fallback
  parse.py               parse_preferences (structured output, rules fallback, clarifying questions)
  tools.py               tool schemas, execution and final plan assembly
  checks.py              estimate_cost and validate_plan
  places.py              Geoapify + Photon, curated fill, generic suggestions
  gateway.py, maps.py, geo.py, weather.py, hours.py, pricing.py, fallback_data.py, prompts.py, models.py
evals/run_evals.py       10 scenarios with automatic checks
docs/SYSTEM_DESIGN.md    architecture, agent loop, validation rules, data and failure handling
```

## How I used AI tools

I built this with Claude Code as a pair programmer: it scaffolded the Next.js + FastAPI app, wrote much of the agent, tools and validator, and ran the browser tests and evals. I made the product and architecture calls (gathering data in code so the model plans in about two calls, a deterministic validator the model has to satisfy, live map data instead of mocks, and moving to Vercel AI Gateway when the Gemini free tier kept returning 503s), and used the eval set to catch and fix weak spots.
