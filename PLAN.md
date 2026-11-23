# Perfect Saturday Planner: Implementation Plan

> **Update (built):** the backend moved from a Next.js route to **Python (FastAPI)** with **Gemini** (google-genai SDK, function calling, model fallback chain), deployed with Next.js as two Vercel Services (`vercel.json`). Places come from OpenStreetMap through **Overpass and Photon** in parallel, because the public Overpass servers were overloaded in testing. The rest of this plan (tools, trace, failure cases) is as built. See README.md for current details.

A hosted AI agent that takes a city, budget, time, mood, interests and constraints and returns a specific, realistic Saturday plan. It explains why each stop fits, shows a live trace of what the agent did, and degrades gracefully when things go wrong. Deployed on **Vercel**.

---

## 1. Requirements → how we meet them

| Assignment asks for | How this plan delivers it |
|---|---|
| Hosted UI with public URL | Next.js app on Vercel; UI and API in one repo |
| Understand preferences | `parsePreferences` step: normalizes form JSON *or* free text into a typed `Preferences` object (minutes, energy level, hard vs soft constraints) |
| ≥ 3 tools, not one giant prompt | 6 tools in a real tool-use loop: the model decides what to call, and the server executes |
| Realistic, specific plan | Real places from OpenStreetMap near one area, travel time between stops, est. costs, Saturday weather |
| Explain why each part fits | `why_it_fits` required per stop in the final schema, tied to mood, interests and constraints |
| Handle ≥ 1 failure gracefully | 8 handled cases (section 6), several visible in the demo |
| Show a trace | Live streaming trace panel: each tool call, its input, result summary, duration, live vs fallback data |
| **Bonus:** real data | OpenStreetMap Overpass (places) + Open-Meteo (geocoding + weather). Free, no API keys |
| **Bonus:** streaming "thinking" trace | Server-Sent Events from the agent loop; Claude's progress notes stream between tool calls |
| **Bonus:** fallback plan | Curated per-city dataset when live data fails or nothing matches, plus a "rainy day / plan B" in every result |
| **Bonus:** clarifying questions | If city is missing or input is too vague, the agent returns 1–2 questions instead of guessing |
| **Bonus:** trade-offs | Final schema has a `tradeoffs[]` field; validator flags over-budget or long-travel items the model must justify |
| **Bonus:** avoid unrealistic suggestions | Deterministic `validatePlan` checks time, budget, distance, hard constraints, weather and opening hours |

---

## 2. Stack

| Layer | Choice | Why |
|---|---|---|
| Framework | **Next.js (App Router) + TypeScript + Tailwind** | First-class on Vercel; UI and API route in one deploy |
| LLM | **Claude via `@anthropic-ai/sdk`**, model `claude-opus-5-5` (set by `ANTHROPIC_MODEL` env var) | Strong tool use; adaptive thinking at `low`/`medium` effort keeps latency reasonable |
| Validation | **Zod** | Validate every tool input before executing it |
| Places | **OpenStreetMap Overpass API** | Real POIs worldwide, free, no key. "Resourceful" bonus |
| Geocoding + weather | **Open-Meteo** (geocoding + forecast APIs) | Free, no key, fast, gives Saturday's hourly rain and temperature |
| Fallback data | Curated JSON (Bangalore, Mumbai, Delhi, + generic template) | Used when Overpass is slow or down, or returns nothing |
| Storage | None | Stateless; no DB needed |

---

## 3. Architecture

```
 Browser (form or free text)
        │  POST /api/plan        ◄── Server-Sent Events stream back
        ▼
 app/api/plan/route.ts  (Vercel Node function, maxDuration 120s)
        │
        ├─ 1. parsePreferences ── LLM structured output → Preferences
        │        └─ vague / no city?  → emit `clarify` (1–2 questions) → stop
        │
        ├─ 2. Agent loop (Claude + tools, max ~8 turns)
        │        ├─ get_weather        (Open-Meteo)
        │        ├─ find_activities    (Overpass → fallback JSON)
        │        ├─ find_food          (Overpass → fallback JSON)
        │        ├─ estimate_cost      (deterministic)
        │        ├─ validate_plan      (deterministic)
        │        └─ submit_plan        (strict schema = final answer)
        │     every call/result → emit `trace` event
        │
        └─ 3. Server re-runs validate_plan on the submitted plan (hard gate)
                 ├─ errors & retries left → send issues back to the model
                 └─ ok (or out of retries) → emit `plan` (+ warnings) → `done`
```

**Why a real loop instead of a fixed pipeline:** the model chooses what to look up (it skips weather for an indoor-only request, or re-queries food with a wider radius when nothing matched). The server still owns the guardrails: tool execution, validation, iteration cap. The system prompt encourages **parallel tool calls** (weather + activities + food in one turn) to cut round trips.

### Tools

| Tool | Input | Does | Data source | When it fails |
|---|---|---|---|---|
| `get_weather` | city/coords | Saturday hourly rain %, temp, sunset | Open-Meteo | returns `unknown`; agent favours a mixed indoor/outdoor plan |
| `find_activities` | coords, interests, energy, indoor_only, radius | Parks, lakes, museums, galleries, live-music venues, bookstores, viewpoints, markets | Overpass, mapped interest → OSM tags | timeout → curated list (marked `fallback`); empty → widen radius once |
| `find_food` | coords, budget/person, diet constraints, near point | Cafés and restaurants honouring `diet:vegetarian`, cuisine tags | Overpass + price heuristics | same as above |
| `estimate_cost` | list of stops | Entry fees + food + travel (haversine distance × local auto/cab rate) | deterministic | n/a |
| `validate_plan` | stops + preferences | Checks total time incl. travel ≤ available, cost ≤ budget, hard constraints met, distances sane, rain vs outdoor stops, opening hours when tagged, crowd heuristics | deterministic | returns `errors[]` (must fix) and `warnings[]` (explain as trade-off) |
| `submit_plan` | final plan | Ends the loop. Timeline, `why_it_fits`, `tradeoffs`, cost breakdown, plan B | strict schema | re-validated server-side |

**Honest-data note:** OSM has no prices or crowd levels. Costs come from a small price table (category × country tier) and crowd levels from heuristics (malls and big tourist attractions on a Saturday afternoon count as crowded). The UI labels both "est." so we never pretend these are real numbers.

### Streaming events (SSE)

`status` · `thinking` (progress notes) · `tool_call` (name, input) · `tool_result` (summary, ms, `live`/`fallback`) · `clarify` · `plan` · `error` · `done`

---

## 4. Project structure

```
app/
  page.tsx                 UI: input panel, trace panel, plan view
  api/plan/route.ts        SSE endpoint, runs the agent
lib/
  agent/
    run.ts                 tool-use loop + event emitter
    parse.ts               parsePreferences (structured output)
    prompts.ts             system prompt
    toolDefs.ts            tool schemas (Zod + JSON schema) + dispatch
  tools/
    geocode.ts             Open-Meteo geocoding
    weather.ts             Open-Meteo forecast
    places.ts              Overpass queries → normalized Place[]
    cost.ts                estimateCost
    validate.ts            validatePlan
  data/
    fallback/*.json        curated places per city
    prices.ts              price heuristics
  types.ts                 Preferences, Place, Stop, Plan
README.md                  run instructions + AI-usage notes
```

---

## 5. UI

- **Input:** a form prefilled with the assignment's example (city, budget, time, mood, interest chips, constraint chips, optional neighbourhood), plus a "just describe it" free-text toggle.
- **Demo presets:** *Tired in Bangalore* · *₹500 student in Mumbai* · *Rainy Delhi* · *Vague: "something fun"* (shows the clarifying questions).
- **Trace panel:** streams step by step with icons, collapsible tool input/output JSON, timings, and a `live` / `fallback` badge.
- **Plan view:** timeline cards (time, place, est. cost, travel from previous stop, *why it fits*, map link), a summary bar (cost vs budget, time vs available, weather), trade-off callouts, and plan B.
- Works at phone width.

---

## 6. Failure cases handled

1. **Vague input** ("something fun this weekend") → 1–2 clarifying questions, answered inline, then re-run.
2. **Unknown city / geocode miss** → clarifying question with suggestions.
3. **Overpass slow or down** (it happens often) → 6s timeout, then curated fallback; trace shows `fallback`.
4. **No options match constraints** → widen radius, relax *soft* constraints only, then fallback plan with an explanation.
5. **Impossible budget or time** (₹100, 1 hour) → free or near-free plan that says what was cut and why.
6. **Rain forecast** → swap outdoor stops for indoor ones; plan B always included.
7. **Validation keeps failing** → up to 2 revision rounds, then return the best plan with visible warnings.
8. **LLM / network error** → SDK retries 429/5xx automatically; otherwise a friendly error card with a Retry button. Never a blank screen.

---

## 7. LLM configuration

- `claude-opus-5-5` with adaptive thinking, `effort: "low"` for parsing and `"medium"` for the agent loop. Model is an env var, so switching to Sonnet 5.5 or Haiku 5.5 for speed or cost is a one-line change (your call).
- Tool choice `auto` + `strict: true` tool schemas; the system prompt tells the model to finish with `submit_plan` (forced tool choice isn't supported on this model).
- Server-side refusal fallback enabled (`fallbacks: "default"`) so a rare safety-classifier false positive doesn't kill a run.
- Guardrails for a public URL: input length limits, max 8 loop turns, capped `max_tokens`, and no user content in URLs.

---

## 8. Vercel deployment

1. `git init`, push to a new GitHub repo (public, so you can submit the link).
2. On vercel.com: **Add New → Project → import the repo** (framework auto-detected as Next.js).
3. Add env var `ANTHROPIC_API_KEY` (and optionally `ANTHROPIC_MODEL`) under Project → Settings → Environment Variables.
4. Deploy → live URL. Every `git push` to `main` redeploys automatically.
5. Route config: `runtime = "nodejs"`, `maxDuration = 120`. Streaming responses work on all plans.

We deploy a hello-world **in the first 15 minutes** to de-risk hosting before building features.

---

## 9. Build order (fits the 2-hour limit)

| Time | Step |
|---|---|
| 0:00–0:15 | Scaffold Next.js + Tailwind, push to GitHub, first Vercel deploy |
| 0:15–0:50 | Data tools: geocode, weather, Overpass places + fallback JSON, cost, validate (unit-check each with a quick script) |
| 0:50–1:20 | `parsePreferences`, agent loop, SSE streaming endpoint |
| 1:20–1:45 | UI: form, presets, live trace, plan rendering |
| 1:45–2:00 | Run all 8 failure cases on the live URL, write README (run steps + AI-usage notes), final deploy |

---

## 10. What you need to provide

- An **Anthropic API key**. You add it yourself to `.env.local` locally and to Vercel env vars; don't paste it in chat.
- A **Vercel account** connected to your GitHub (`valayash`).
- OK to create a **public GitHub repo** (suggested name: `perfect-saturday-planner`).

## 11. Submission checklist

- [ ] Live Vercel URL
- [ ] GitHub repo link
- [ ] README with local run instructions
- [ ] 2–3 lines on how AI tools were used (in README)
- [ ] Optional: Loom demo under 2 minutes (vague-input clarify → full plan → trace → rainy/fallback case)
