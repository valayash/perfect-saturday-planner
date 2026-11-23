# System Design: Perfect Saturday Planner Agent

This document describes the whole system: what each part does, how a request flows through it, how the agent decides and checks its work, and how it behaves when its dependencies fail.

---

## 1. Goals and non-goals

**Goals**
- Turn a person's city, budget, time, mood, interests and constraints (as a form or free text) into a **specific, realistic Saturday plan** built from real places.
- Use an **agent with tools**, not one giant prompt: the model decides what to look up, and the server owns facts and rules.
- **Explain** why each stop fits the person, and state trade-offs honestly.
- Show a **live trace** of every step.
- **Degrade gracefully**: there's always a useful answer, even when the model, the map APIs or the weather API are down.
- Run as a **hosted web app** on Vercel, with no local setup for reviewers.

**Non-goals**
- Bookings, payments or accounts.
- Exact prices or crowd levels (no free source exists; they're labelled estimates).
- Multi-day itineraries.

---

## 2. Architecture at a glance

```mermaid
flowchart LR
    U["Browser<br/>Next.js UI"] -- "POST /api/plan" --> R{"Vercel routing<br/>vercel.json rewrites"}
    R -- "/*" --> W["web service<br/>Next.js 16"]
    R -- "/api/*" --> A["api service<br/>FastAPI on Python 3.12"]
    A -- "SSE stream:<br/>trace + plan" --> U

    subgraph Agent["Agent runtime (inside the api service)"]
        P["parse_preferences"] --> L["resolve_location"] --> LOOP["Gemini function-calling loop"]
        LOOP --> T["Tools"]
        T --> V["Deterministic checks<br/>estimate_cost / validate_plan"]
    end
    A --- Agent

    LOOP -- "generateContent (stream)" --> G[("Gemini API<br/>model fallback chain")]
    P -- "structured JSON" --> G
    T -- "forecast" --> OM[("Open-Meteo<br/>weather + geocoding")]
    T -- "POIs, rich tags" --> OP[("OSM Overpass")]
    T -- "POIs, fast search" --> PH[("OSM Photon")]
    T -. "if live data fails" .-> CU[("Curated city data")]
```

| Part | Tech | Responsibility |
|---|---|---|
| **web** service | Next.js 16, React 19, Tailwind 4 | Form or free-text input, clarification prompts, live trace, plan rendering |
| **api** service | FastAPI, Python 3.12 | Streams the agent run as Server-Sent Events; rate limiting; keep-alives |
| Agent runtime | `google-genai` SDK | Preference parsing, the tool loop, model fallback, rule-based fallback |
| Tools | Python, `httpx2` | Weather, places, cost and validation; all facts come from here |
| External data | Open-Meteo, OSM Overpass, OSM Photon | Free, keyless, real data |

Both services ship as **one Vercel project** using [Vercel Services](https://vercel.com/docs/services). The browser talks only to the site's own origin, so there's no CORS, and the API key stays server-side.

---

## 3. Request lifecycle

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant UI as Next.js UI
    participant API as FastAPI /api/plan
    participant Agent as Agent runtime
    participant Gemini
    participant Data as Open-Meteo / OSM

    User->>UI: fill form or describe it, press Plan
    UI->>API: POST /api/plan (JSON)
    API-->>UI: 200 text/event-stream (stays open)
    API->>Agent: run(request)

    Agent->>Gemini: parse_preferences (structured JSON)
    Gemini-->>Agent: Preferences
    Agent-->>UI: step_end, preferences
    alt input too vague / no city
        Agent-->>UI: clarify (1-2 questions)
        UI->>User: show questions
        User->>UI: answers
        UI->>API: POST /api/plan + clarifications
    end

    Agent->>Data: geocode city + neighbourhood
    Agent-->>UI: step_end (resolve_location)

    loop until submit_plan accepted (max 10 turns, 240 s)
        Agent->>Gemini: contents + tools (streamed)
        Gemini-->>Agent: thoughts + function calls
        Agent-->>UI: thought_*, step_start (per call)
        par tool calls run concurrently
            Agent->>Data: get_weather / find_activities / find_food
        end
        Agent-->>UI: step_end (per call)
        Agent->>Gemini: function responses
    end

    Agent-->>UI: plan (+ warnings), done
    UI->>User: timeline, costs, trade-offs, plan B, trace
```

---

## 4. The agent

### 4.1 Three phases

| Phase | What happens | LLM? |
|---|---|---|
| 1. `parse_preferences` | Normalise the input into a typed `Preferences` object: city, area, budget, currency, minutes, start time, mood, **energy level**, interests, **hard vs soft constraints**, assumptions. Decides whether to ask clarifying questions. | Gemini structured output, with a rule-based fallback |
| 2. `resolve_location` | Geocode the city (the most populous match) and neighbourhood (the match nearest the city). An unknown city becomes a clarifying question. | No |
| 3. Tool loop | Gemini plans with the six tools below until `submit_plan` is accepted. | Gemini function calling |

Parsing is a separate, cheap call on purpose. It keeps clarification logic out of the planning loop, and the loop always starts from clean, validated preferences.

### 4.2 Tools

| Tool | Input (validated with Pydantic) | Output to the model | Notes |
|---|---|---|---|
| `get_weather` | none | Saturday's hourly rain chance, temperature, sky; sunset | Uses the run's resolved location and time window |
| `find_activities` | `categories[]` (enum of 20), `radius_km`, `indoor_only`, `near_place_id?` | Up to 12 places: `place_id`, distance, indoor, est. cost, crowd estimate, hours | Category diversity via round-robin selection |
| `find_food` | `categories[]` (enum of 5), `radius_km`, `max_price_per_person?`, `near_place_id?` | Up to 10 places with veg status (`only`/`yes`/`unverified`) | Hard diet constraints filter results server-side |
| `estimate_cost` | `stops[]` (place_id, start, duration, spend?) | Per-stop spend, travel legs, total vs budget | Travel: walk if ≤ 1.2 km, else auto/cab (road factor 1.35) |
| `validate_plan` | `stops[]` | `errors[]` (must fix), `warnings[]` (fix or explain), totals | See the rules in §6 |
| `submit_plan` | title, summary, stops with `activity`/`why_it_fits`/`tip`, `tradeoffs[]`, `plan_b` | Accepted, or rejected with errors | Re-validated server-side; rejected up to 2 times |

**Grounding guarantee:** every place a search tool returns is stored in a per-run registry (`RunContext.places`). Plans may only reference `place_id`s from that registry, so the model **can't invent a venue**. An unknown id is a validation error.

### 4.3 Loop state machine

```mermaid
stateDiagram-v2
    [*] --> ModelTurn
    ModelTurn --> RunTools: function calls
    ModelTurn --> Nudge: text only
    Nudge --> ModelTurn: "validate, then submit_plan"
    RunTools --> ModelTurn: function responses
    RunTools --> Revalidate: submit_plan
    Revalidate --> ModelTurn: errors and attempts < 2
    Revalidate --> Done: accepted (warnings attached)
    ModelTurn --> SwitchModel: 503/429/timeout before first good turn
    SwitchModel --> ModelTurn: next model in chain
    ModelTurn --> Retry: 503/429/timeout mid-run
    Retry --> ModelTurn: backoff 1.5s, 3s
    Retry --> Handoff: retries exhausted
    Handoff --> ModelTurn: next model, fresh history seeded with weather, places, last draft
    Handoff --> RuleBased: no models left
    ModelTurn --> RuleBased: API error / turn limit / deadline
    RuleBased --> Done
    Done --> [*]
```

### 4.4 Prompting principles
- **Stable system prompt** (no dates or request data) describes the process: first turn calls weather, activities and food in parallel; 2–4 clustered stops; validate before submitting; plan B is mandatory; unmet interests go in trade-offs.
- **Per-request facts** go in the first user message: location label, time window, budget, preferences JSON.
- **User free text** is wrapped in `<user_text>` tags and treated as data.
- The model writes `why_it_fits` per stop and must tie it to a named preference (mood, interest, constraint, weather).

---

## 5. Model strategy (Gemini)

| Use | Default model | Thinking | Why |
|---|---|---|---|
| Preference parsing | `gemini-3.5-flash-lite` | `low` | Fast, cheap, reliable structured JSON |
| Agent loop | chain: `gemini-3.5-flash-lite` → `gemini-3.8-flash` → `gemini-4-hydrogen` | `low` (Hydrogen: `medium`) | During testing the larger models often returned 503 "high demand"; Flash-Lite answered in ~1–2 s |

All of it is configurable with `GEMINI_MODELS` and `GEMINI_PARSE_MODEL`.

**Thought signatures.** Gemini 3+/4 attach signatures to function-call parts and require them back in the next request. The loop appends the model's parts **exactly as streamed**, empty parts included, and never merges them.

**Why model switching only happens before the first good turn:** the signatures tie the conversation history to the model that produced them. After the first good turn, failures are retried on the same model with backoff. If that still fails, the run **hands off** to the next model: a fresh conversation seeded with the forecast, every place found so far (their `place_id`s stay valid because they live in the run's registry) and the last draft. Only when no models are left does it drop to the rule-based planner, which reuses the same tools. Agent turns time out after 20 s, so an overloaded model fails fast.

---

## 6. Plan validation rules (`validate_plan`)

| Check | Error (must fix) | Warning (fix or explain) |
|---|---|---|
| Grounding | `place_id` not returned by a search | |
| Time window | starts > 15 min before the user is free; ends > 15 min after the cutoff | uses < 50% of available time |
| Travel feasibility | gap between stops < travel time − 10 min; any leg > 15 km | leg > 8 km; travel > 30% of the plan |
| Budget | total > 115% of budget | total > 100% of budget |
| Opening hours (OSM `opening_hours`, Saturday) | parsed hours show the place is closed | hours unknown for live places |
| Hard diet constraint | food place tagged non-veg | veg status unverified |
| Crowds (soft constraint) | | likely-crowded place on a Saturday |
| Weather | | outdoor stop with ≥ 60% rain chance; outdoor stop past sunset |
| Energy | | > 3 stops for low energy |
| Interests | | food interest but no food stop |
| Data quality | | generic suggestion used |

Costs come from `estimate_cost`: per-category price heuristics (INR for India, a generic table elsewhere) plus distance-based travel. The opening-hours parser understands common OSM forms such as `Mo-Su 09:00-21:00; Su off` and `24/7`. Anything fancier counts as unknown rather than a guess.

---

## 7. Data layer

### 7.1 Places provider chain

```mermaid
flowchart TD
    Q["find_activities / find_food"] --> PAR{{"query in parallel"}}
    PAR --> OV["Overpass<br/>5 s timeout<br/>rich tags: hours, diet, cuisine"]
    PAR --> PH["Photon<br/>≤ 3 concurrent, retry on 503<br/>names, addresses, coordinates"]
    OV -- "results" --> PICK["prefer Overpass"]
    OV -- "fail → 10 min cooldown" --> PH2["use Photon results"]
    PH --> PH2
    PICK --> F["filter: indoor, diet, price<br/>dedupe, score, diversify"]
    PH2 --> F
    F -- "≥ 3 results" --> OUT["return 'live'"]
    F -- "< 3 results" --> WIDEN["widen radius ×2 once"] --> F
    F -- "nothing" --> CUR["curated places<br/>Bengaluru / Mumbai / Delhi"] --> OUT2["return 'fallback'"]
    CUR -- "unknown city" --> GEN["honest generic suggestions"] --> OUT3["return 'generic'"]
```

- **Why two live sources:** the public Overpass servers were frequently at capacity during development ("all query workers are busy"). Photon answers in under a second but has no opening-hours or diet tags, so validation is more cautious with its results.
- **Vegetarian heuristics** for untagged places: names with "veg", "vegetarian" or "vegan" count as veg. Names like "Sagar", "Bhavan", "Udupi" or "Darshini" are likely veg but marked *unverified*. Names with meat words are excluded.
- **Crowd heuristic:** a per-category baseline, raised one level for well-known places (Wikidata or Wikipedia tags).

### 7.2 Weather and geocoding
- **Open-Meteo forecast** for the coming Saturday in the city's timezone: hourly rain chance and temperature for the plan window, plus sunset.
- **Open-Meteo geocoding**, with aliases (Bangalore → Bengaluru) and the most populous match.
- Geocoding results are cached in memory per instance.

---

## 8. Streaming protocol (SSE)

`POST /api/plan` returns `text/event-stream`. Each event is `data: <json>\n\n`. A `: keep-alive` comment goes out every 5 s of silence so proxies don't close the stream.

| Event | Payload | UI use |
|---|---|---|
| `step_start` | `id, name, label, input` | New trace row with a spinner |
| `step_end` | `id, ok, summary, source, ms, output` | Tick or cross, timing, `live`/`fallback`/`gemini`/`rules` badge; expandable input and output JSON |
| `thought_start` / `thought_delta` | `block, kind` / `block, text` | "Thinking with <model>…" and any thought summaries, streamed |
| `status` | `message, tone` | Model switches, retries, fallback notices |
| `preferences` | parsed `Preferences` | "What I understood" chips |
| `clarify` | `questions[], assumptions[]` | Inline question form; answers are re-sent as `clarifications` |
| `plan` | final plan, `mode` (`agent` or `rules`), `model` | Plan view |
| `error` | `message, retryable` | Error card with a Retry button |
| `done` | `seconds` | Stops timers |

Example:
```
data: {"type":"step_start","id":"s4","name":"find_food","label":"Finding food that fits","input":{"categories":["cafe","restaurant"],"radius_km":2}}

data: {"type":"step_end","id":"s4","ok":true,"summary":"10 food spots (live OpenStreetMap)","source":"live","ms":5374,"output":{...}}
```

---

## 9. Failure handling

| Failure | Detection | Behaviour | Shown to the user |
|---|---|---|---|
| Vague input / no city | parse result | 1–2 clarifying questions, then a re-run with the answers | Question card |
| Unknown city | geocoder returns nothing | Clarifying question | Question card |
| Gemini overloaded / rate-limited (first turn) | 503, 429, timeout | Next model in the chain | ℹ️ status in the trace |
| Gemini overloaded mid-run | same | 2 retries on the same model, then hand off to the next model with a summary of the work so far, then the rule-based plan | ℹ️ / ⚠️ status |
| Gemini parse fails | any exception or 12 s per-model timeout (20 s total budget) | Next model, then the rule-based parser (reads form, free text and answers) | `RULES` badge |
| Gemini unavailable (bad key, outage) | API error | Rule-based plan using the same tools and validator | "Rule-based backup plan" label |
| Overpass at capacity | HTTP error or timeout | Photon results; Overpass skipped for 10 min | `live` (Photon) |
| No live places | empty results | Widen radius, then curated, then generic | `fallback` / `generic` badge, "curated data" pill |
| Weather API down | exception | Plan for uncertainty; plan B covers rain | Trace summary |
| Invalid tool input from the model | Pydantic validation | Error returned as the function response; the model retries | ✕ in the trace |
| Plan breaks rules | `validate_plan` errors | Sent back to the model (up to 2 rejections), then accepted with visible warnings | "Heads-up" box |
| Safety block | finish reason | Error message, not retryable | Error card |
| Too slow | 240 s deadline (Vercel limit 300 s) | Rule-based plan | ⚠️ status |
| Abuse | per-IP limit, 10 requests / 10 min | HTTP 429 | Error card |

---

## 10. Frontend

```
app/components/
  Planner.tsx     state machine: idle → running → clarify | done | error; owns the SSE stream
  InputPanel.tsx  form or free text, presets, interest and constraint chips
  TracePanel.tsx  live steps (icons, timings, source badges, expandable JSON), thoughts, statuses
  PlanView.tsx    header, cost / time / travel meters, timeline with travel legs, trade-offs, plan B, warnings, cost table
app/lib/
  stream.ts       fetch + ReadableStream SSE parser
  types.ts        shared event and plan types
```

The UI is fully client-side and reads the stream with `fetch` and a `ReadableStream` reader (not `EventSource`, which can't POST). Light and dark themes come from CSS variables. It works at phone width.

---

## 11. Deployment

```json
{
  "services": {
    "web": { "root": "frontend/", "framework": "nextjs" },
    "api": { "root": "backend/", "framework": "fastapi", "entrypoint": "main:app" }
  },
  "rewrites": [
    { "source": "/api/(.*)", "destination": { "service": "api" } },
    { "source": "/(.*)", "destination": { "service": "web" } }
  ]
}
```

- **Runtime limits:** Vercel Hobby with Fluid compute allows 300 s per function. The agent caps itself at 240 s. Python functions support streaming responses.
- **Secrets:** `GEMINI_API_KEY` lives only in Vercel environment variables (and in a gitignored `backend/.env` locally). It's never sent to the browser.
- **Local dev:** `uvicorn` on :8000. Next.js dev rewrites `/api/*` to it, matching production routing.

---

## 12. Security and abuse
- **Input limits:** Pydantic caps string lengths, list sizes and budget range.
- **Prompt injection:** user text is wrapped and labelled as data. Tool results are data from public map APIs. The model can only *propose* places from tool results, and server-side validation enforces the rules.
- **Grounding:** the place registry prevents invented venues and links.
- **Rate limiting:** per-IP and per-instance. Good enough to protect a demo key; a shared store such as Redis would be needed at scale.
- **XSS:** React escapes all model text; links are built server-side from place names.

---

## 13. Trade-offs and known limitations
- **Estimates, not facts:** prices and crowds are heuristics, and Photon results lack opening hours. The UI says so everywhere.
- **Public APIs are best-effort:** Overpass and the larger Gemini models were often overloaded during development. The fallback chains are the main mitigation.
- **One person, one afternoon:** no group logic, multi-day plans or bookings.
- **Per-instance state:** the rate limiter and caches don't persist across serverless instances.

## 14. Future work
- Real price and rating data from a keyed provider (Google Places or Foursquare) behind the same `find_*` interface.
- Event listings (gigs, exhibitions) for the specific Saturday.
- Shareable plan links (store plans in a key-value store).
- Persisted rate limiting and caching with Redis.
- An evaluation set of personas to score plan quality as prompts and models change.

---

## 15. Repository map

```
vercel.json                      services + routing
docs/SYSTEM_DESIGN.md            this document
frontend/                        Next.js app (web service)
backend/main.py                  FastAPI app: /api/plan (SSE), /api/health
backend/planner/agent.py         agent loop, model chain, rule-based plan
backend/planner/parse.py         parse_preferences (Gemini + rules)
backend/planner/prompts.py       system prompts
backend/planner/tools.py         tool schemas, execution, final plan assembly
backend/planner/checks.py        estimate_cost, validate_plan, RunContext
backend/planner/places.py        Overpass + Photon + curated + generic
backend/planner/geo.py           geocoding, distance, travel legs
backend/planner/weather.py       Saturday forecast
backend/planner/hours.py         OSM opening_hours (Saturday subset)
backend/planner/pricing.py       category metadata, price and crowd heuristics
backend/planner/fallback_data.py curated places
```
