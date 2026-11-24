#!/usr/bin/env python3
"""A small fixed test set for the planner, run against the live API (or a local one).

Each scenario sends one request and checks the result automatically: did it plan (or ask, when it should),
does the plan pass the server's own validation, stay within budget and the time window, respect diet,
weather and crowds, and cover what was asked for. It also records how long each run took and how many LLM
calls it used.

    python3 evals/run_evals.py                                  # the live site
    python3 evals/run_evals.py --base http://localhost:8000     # a local backend
    python3 evals/run_evals.py --only spec-example,rain
    python3 evals/run_evals.py --repeat 3                       # LLM output varies: how often does each pass?

Writes evals/RESULTS.md. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LIVE = "https://perfect-saturday-planner-nine.vercel.app"
OUT = Path(__file__).with_name("RESULTS.md")
FOOD = {"cafe", "restaurant", "fast_food", "dessert", "street_food", "bar"}
OUTDOOR_WALKS = {"park", "garden", "lake", "viewpoint", "beach"}


class Run:
    """One finished request: its events and the parts the checks look at."""

    def __init__(self, events: list[dict], wall_s: float):
        first = lambda kind: next((e for e in events if e.get("type") == kind), None)  # noqa: E731
        plan_ev = first("plan") or {}
        self.events = events
        self.plan: dict | None = plan_ev.get("plan")
        self.mode = plan_ev.get("mode")
        self.prefs = (first("preferences") or {}).get("preferences") or {}
        self.clarify = first("clarify")
        self.error = (first("error") or {}).get("message")
        self.done = first("done") or {}
        self.seconds = self.done.get("seconds") or round(wall_s, 1)

    @property
    def stops(self) -> list[dict]:
        return (self.plan or {}).get("stops", [])

    def said(self) -> str:
        """Everything the plan says, lower-cased, for "is X mentioned" checks."""
        p = self.plan or {}
        parts = [p.get("title", ""), p.get("summary", ""), *p.get("tradeoffs", []), (p.get("plan_b") or {}).get("suggestion", "")]
        for s in self.stops:
            parts += [s.get("activity") or "", s.get("why_it_fits") or "", s.get("tip") or ""]
        return " ".join(parts).lower()

    def tradeoffs(self) -> str:
        return " ".join((self.plan or {}).get("tradeoffs", [])).lower()


def to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


# ---------- checks: each returns (passed, detail shown when it fails) ----------

def passes_validation(r: Run):
    n = r.plan["validator_issues"]
    return n == 0, f"{n} validation error(s) left in the final plan: " + " | ".join(e[:110] for e in r.plan["warnings"][:n])


def stop_count(r: Run):
    n = len(r.stops)
    return 2 <= n <= 5, f"{n} stops"


def every_stop_explained(r: Run):
    thin = [s["name"] for s in r.stops if len((s.get("why_it_fits") or "").strip()) < 25]
    return not thin, "no real reason given for " + ", ".join(thin)


def no_repeated_place(r: Run):
    names = [s["name"] for s in r.stops]
    return len(set(names)) == len(names), "the same place appears twice"


def within_budget(r: Run):
    c = r.plan["cost"]
    ok = c["total"] <= c["budget"] * 1.15 or "budget" in r.tradeoffs()
    return ok, f"{c['total']} of {c['budget']} {c['currency']} with no trade-off explaining it"


def inside_time_window(r: Run):
    start = to_min(r.prefs["start_time"])
    end = start + r.prefs["available_minutes"]
    first, last = to_min(r.stops[0]["start"]), to_min(r.stops[-1]["end"])
    return first >= start - 15 and last <= end + 15, f"stops run {r.stops[0]['start']}–{r.stops[-1]['end']}, window {r.prefs['start_time']} + {r.prefs['available_minutes']} min"


def has_route_map(r: Run):
    return bool(r.plan.get("map_image")), "no route map"


def vegetarian_food(r: Run):
    bad = [s["name"] for s in r.stops if s["category"] in FOOD and s.get("veg") == "no"]
    return not bad, "non-vegetarian place: " + ", ".join(bad)


def has_food_stop(r: Run):
    return any(s["category"] in FOOD for s in r.stops), "no food stop although food was asked for"


def quiet_or_explained(r: Run):
    busy = [s["name"] for s in r.stops if s.get("crowd") == "high"]
    return not busy or "crowd" in r.tradeoffs(), "likely-crowded stop with no trade-off: " + ", ".join(busy)


def music_served_or_explained(r: Run):
    real = any(s["category"] == "live_music" or (s["category"] in ("theatre", "arts_centre") and re.search(r"music|sangeet|jazz|sabha|gig|concert", s["name"], re.I)) for s in r.stops)
    return real or "music" in r.tradeoffs(), "music isn't served by a music venue and no trade-off says so"


def indoor_in_rain(r: Run):
    out = [s["name"] for s in r.stops if not s.get("indoor")]
    return not out or "rain" in r.tradeoffs(), "outdoor stop in forecast rain with no trade-off: " + ", ".join(out)


def asks_for_city(r: Run):
    qs = json.dumps((r.clarify or {}).get("questions", [])).lower()
    return r.clarify is not None and "city" in qs, "didn't ask which city"


def does_not_plan_yet(r: Run):
    return r.plan is None, "planned without asking"


def has_category(cats: set[str], label: str):
    def check(r: Run):
        return any(s["category"] in cats for s in r.stops), f"no {label} stop"
    check.__name__ = f"has {label} stop"
    return check


def served_or_explained(cats: set[str], word: str):
    def check(r: Run):
        return any(s["category"] in cats for s in r.stops) or word in r.tradeoffs(), f"no {word} stop and no trade-off explaining why"
    check.__name__ = f"{word}: served or explained"
    return check


def food_matches(label: str, pattern: str):
    def check(r: Run):
        hit = any(re.search(pattern, f"{s['name']} {s.get('activity')} {s.get('why_it_fits')}", re.I) for s in r.stops if s["category"] in FOOD)
        return hit, f"no food stop that is {label}"
    check.__name__ = f"{label} food"
    return check


def free_stop(r: Run):
    return any(s["est_cost"] == 0 for s in r.stops), "no free stop on a tight budget"


def starts_at(hhmm: str):
    def check(r: Run):
        return r.prefs.get("start_time") == hhmm, f"start read as {r.prefs.get('start_time')}"
    check.__name__ = f"start time {hhmm}"
    return check


def local_currency(r: Run):
    cur = r.plan["cost"]["currency"]
    return cur != "INR", f"priced in {cur}"


def rule_based_plan(r: Run):
    return r.mode == "rules" and not r.done.get("llm_calls"), f"mode {r.mode}, {r.done.get('llm_calls')} LLM calls"


COMMON = [passes_validation, stop_count, every_stop_explained, no_repeated_place, within_budget, inside_time_window, has_route_map]

SCENARIOS = [
    {
        "id": "spec-example", "title": "The assignment's example input",
        "request": {"city": "Bangalore", "budget": 2000, "available_time": "4 hours", "mood": "tired but wants to do something fun",
                    "interests": ["food", "music", "walks"], "constraints": ["vegetarian", "avoid crowded places"]},
        "checks": COMMON + [vegetarian_food, has_food_stop, quiet_or_explained, music_served_or_explained],
    },
    {
        "id": "vague-chat", "title": "Vague chat request", "expect": "clarify",
        "request": {"free_text": "plan my saturday"},
        "checks": [asks_for_city, does_not_plan_yet],
    },
    {
        "id": "chat-answers", "title": "Vague request + answers (Pune)",
        "request": {"free_text": "I want a chill saturday, maybe some coffee and a walk",
                    "clarifications": [{"question": "Which city (and area) are you in?", "answer": "Pune, Koregaon Park"},
                                       {"question": "Roughly what budget?", "answer": "₹1200"},
                                       {"question": "When are you free?", "answer": "Afternoon, 2–6 pm"}]},
        "checks": COMMON + [starts_at("14:00"), has_category({"cafe"}, "café"), served_or_explained(OUTDOOR_WALKS, "walk")],
    },
    {
        "id": "rain", "title": "Rain all afternoon (simulated)",
        "request": {"city": "Bengaluru", "area": "Indiranagar", "budget": 1500, "available_time": "4 hours from 2pm",
                    "interests": ["walks", "parks", "coffee"], "simulate": ["rain"]},
        "checks": COMMON + [indoor_in_rain],
    },
    {
        "id": "thin-data", "title": "Smaller city with thin map data (Nashik)",
        "request": {"free_text": "I'm in Nashik, free from 5pm for 3 hours, budget 800 rupees, temples and street food"},
        "checks": COMMON + [has_food_stop],
    },
    {
        "id": "tight-budget", "title": "Tight budget (₹300 in Mumbai)",
        "request": {"city": "Mumbai", "area": "Colaba", "budget": 300, "available_time": "5 hours from 1pm",
                    "interests": ["museums", "cafes", "walks"]},
        "checks": COMMON + [free_stop],
    },
    {
        "id": "far-wish", "title": "Wish only available far away (bowling, Gurugram)",
        "request": {"city": "Gurugram", "budget": 2000, "available_time": "from 4pm until 10 pm", "free_text": "bowling and beer with friends"},
        "checks": COMMON + [served_or_explained({"bowling"}, "bowling"), has_category({"bar"}, "bar")],
    },
    {
        "id": "cuisine-history", "title": "History, art and a North Indian lunch (Delhi)",
        "request": {"city": "Delhi", "area": "Hauz Khas", "budget": 2500, "available_time": "11am to 6pm",
                    "free_text": "history and art, good north indian lunch, avoid crowds"},
        "checks": COMMON + [has_category({"historic"}, "historic"), food_matches("north indian", r"north indian|punjabi|mughlai|tandoor|dhaba|butter chicken")],
    },
    {
        "id": "international", "title": "Outside India (London)",
        "request": {"city": "London", "budget": 60, "available_time": "4 hours from 2pm", "interests": ["art", "coffee"]},
        "checks": COMMON + [local_currency, has_category({"gallery", "museum", "arts_centre"}, "art")],
    },
    {
        "id": "llm-down", "title": "Every LLM unavailable (simulated)",
        "request": {"city": "Bengaluru", "area": "Koramangala", "budget": 1500, "available_time": "4 hours from 4pm",
                    "interests": ["books", "coffee"], "constraints": ["vegetarian"], "simulate": ["llm_down"]},
        "checks": COMMON + [rule_based_plan, vegetarian_food],
    },
]


def post(base: str, body: dict) -> tuple[list[dict], float]:
    """POST one plan request (non-streaming). Waits and retries when the rate limiter says so."""
    for attempt in range(8):
        req = urllib.request.Request(f"{base}/api/plan?mode=json", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=290) as resp:
                return json.load(resp)["events"], time.monotonic() - t0
        except urllib.error.HTTPError as err:
            if err.code == 429 and attempt < 7:
                print("    rate-limited; waiting 75 s", flush=True)
                time.sleep(75)
                continue
            raise
    raise RuntimeError("unreachable")


def evaluate(sc: dict, run: Run) -> list[tuple[str, bool, str]]:
    expect = sc.get("expect", "plan")
    if expect == "plan" and run.plan is None:
        why = run.error or ("asked: " + json.dumps(run.clarify.get("questions"))[:120] if run.clarify else "no plan returned")
        return [("returns a plan", False, why)]
    results = []
    for check in sc["checks"]:
        try:
            ok, detail = check(run)
        except Exception as err:  # a malformed result is a failure, not a crash of the whole suite
            ok, detail = False, f"check crashed: {type(err).__name__}: {err}"
        results.append((check.__name__.replace("_", " "), ok, "" if ok else detail))
    return results


def run_once(base: str, sc: dict) -> tuple[Run | None, list[tuple[str, bool, str]]]:
    try:
        events, wall = post(base, sc["request"])
        run = Run(events, wall)
        return run, evaluate(sc, run)
    except Exception as err:
        return None, [("request succeeds", False, f"{type(err).__name__}: {err}")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=LIVE, help=f"API base URL (default: {LIVE})")
    ap.add_argument("--only", default="", help="comma-separated scenario ids")
    ap.add_argument("--repeat", type=int, default=1, help="runs per scenario; LLM output varies, so >1 shows how often each passes")
    ap.add_argument("--out", default=str(OUT), help="where to write the markdown report")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    chosen = [s for s in SCENARIOS if not args.only or s["id"] in args.only.split(",")]

    rows = []  # (scenario, [(run, results, failed), ...])
    for sc in chosen:
        print(f"• {sc['id']}: {sc['title']}", flush=True)
        attempts = []
        for _ in range(args.repeat):
            run, results = run_once(base, sc)
            failed = [(name, detail) for name, ok, detail in results if not ok]
            attempts.append((run, results, failed))
            extra = f" {run.seconds}s, {run.done.get('llm_calls', 0)} LLM calls" if run else ""
            print(f"    {'pass' if not failed else f'FAIL ({len(failed)})'}{extra}" + "".join(f"\n      ✗ {n}: {d}" for n, d in failed), flush=True)
        rows.append((sc, attempts))

    all_runs = [a for _, attempts in rows for a in attempts]
    clean = sum(not failed for _, _, failed in all_runs)
    steady = sum(all(not failed for _, _, failed in attempts) for _, attempts in rows)
    plans = [r for r, _, _ in all_runs if r and r.plan]
    times = [r.seconds for r in plans]
    calls = [r.done.get("llm_calls", 0) for r in plans if r.mode == "agent"]
    runs_note = f" × {args.repeat} runs each" if args.repeat > 1 else ""
    lines = [
        "# Eval results",
        "",
        f"Run against `{args.base}` on {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC with "
        f"`python3 evals/run_evals.py{f' --repeat {args.repeat}' if args.repeat > 1 else ''}` ({len(rows)} scenarios{runs_note}).",
        "",
        (f"**{steady}/{len(rows)} scenarios passed every check in every run ({clean}/{len(all_runs)} runs clean).** " if args.repeat > 1
         else f"**{clean}/{len(rows)} scenarios passed every check.** ")
        + (f"Plans took {min(times):.1f}–{max(times):.1f} s (median {statistics.median(times):.1f} s); " if times else "")
        + (f"agent plans used {statistics.mean(calls):.1f} LLM calls on average." if calls else ""),
        "",
        "| Scenario | Result | Checks | Stops (first passing run) | Time | LLM calls | Tokens |",
        "|---|---|---|---|---|---|---|",
    ]
    for sc, attempts in rows:
        good = sum(not failed for _, _, failed in attempts)
        if args.repeat > 1:
            result = f"{'✅' if good == len(attempts) else '⚠️' if good else '❌'} {good}/{len(attempts)} runs"
        else:
            result = "✅ pass" if good else "❌ fail"
        show = next((a for a in attempts if not a[2]), attempts[0])
        run, results, _ = show
        ok = sum(1 for _, passed, _ in results if passed)
        if run and run.plan:
            stops = " → ".join(s["name"] for s in run.stops)
        elif run and run.clarify:
            stops = "asked: " + ", ".join(q.get("id", "?") if isinstance(q, dict) else "?" for q in run.clarify["questions"])
        else:
            stops = "—"
        done = [r for r, _, _ in attempts if r]
        t = f"{statistics.median([r.seconds for r in done]):.1f} s" if done else "—"
        c = f"{statistics.mean([r.done.get('llm_calls', 0) for r in done]):.1f}".removesuffix(".0") if done else "—"
        tok = f"{round(statistics.mean([r.done.get('tokens', 0) for r in done]))}" if done else "—"
        lines.append(f"| {sc['title']} | {result} | {ok}/{len(results)} | {stops} | {t} | {c} | {tok} |")
    fails = []
    for sc, attempts in rows:
        seen: dict[str, tuple[int, str]] = {}
        for _, _, failed in attempts:
            for name, detail in failed:
                count, first = seen.get(name, (0, detail))
                seen[name] = (count + 1, first)
        fails += [(sc, name, count, len(attempts), detail) for name, (count, detail) in seen.items()]
    if fails:
        lines += ["", "## Failed checks", ""]
        lines += [f"- **{sc['id']}** · {name}" + (f" ({count} of {n} runs)" if n > 1 else "") + f": {detail}" for sc, name, count, n, detail in fails]
    lines += ["", "## What each scenario checks", ""]
    for sc in chosen:
        names = [c.__name__.replace("_", " ") for c in sc["checks"]]
        lines.append(f"- **{sc['id']}**: " + ", ".join(names))
    Path(args.out).write_text("\n".join(lines) + "\n")
    print(f"\n{steady if args.repeat > 1 else clean}/{len(rows)} scenarios passed. Report: {args.out}")
    return 0 if steady == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
