"use client";

import { useRef, useState } from "react";
import { StreamStalled, fetchRunResult, runPlanJson, streamPlan } from "../lib/stream";
import type { AgentEvent, Plan, PlanRequest, Preferences, TraceItem } from "../lib/types";
import InputPanel from "./InputPanel";
import PlanView from "./PlanView";
import TracePanel from "./TracePanel";

type Phase = "idle" | "running" | "clarify" | "done" | "error";

export default function Planner() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [trace, setTrace] = useState<TraceItem[]>([]);
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [plan, setPlan] = useState<{ plan: Plan; mode: "agent" | "rules"; model?: string } | null>(null);
  const [questions, setQuestions] = useState<string[]>([]);
  const [answers, setAnswers] = useState<string[]>([]);
  const [error, setError] = useState<{ message: string; retryable: boolean } | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [doneSeconds, setDoneSeconds] = useState<number | null>(null);
  const [llmStats, setLlmStats] = useState<{ calls: number; tokens: number } | null>(null);
  const lastRequest = useRef<PlanRequest | null>(null);
  const abort = useRef<AbortController | null>(null);
  const resultsRef = useRef<HTMLDivElement>(null);

  const handle = (e: AgentEvent) => {
    switch (e.type) {
      case "step_start":
        setTrace((t) => [...t, { kind: "step", id: e.id, name: e.name, label: e.label, input: e.input, actor: e.actor, status: "running" }]);
        break;
      case "step_end":
        setTrace((t) =>
          t.map((i) =>
            i.kind === "step" && i.id === e.id
              ? { ...i, status: e.ok ? "done" : "error", summary: e.summary, ms: e.ms, source: e.source, output: e.output }
              : i,
          ),
        );
        break;
      case "thought_start":
        setTrace((t) => [...t, { kind: "thought", id: e.block, text: "" }]);
        break;
      case "thought_delta":
        setTrace((t) => t.map((i) => (i.kind === "thought" && i.id === e.block ? { ...i, text: i.text + e.text } : i)));
        break;
      case "status":
        setTrace((t) => [...t, { kind: "status", id: `status-${t.length}`, message: e.message, tone: e.tone }]);
        break;
      case "preferences":
        setPrefs(e.preferences);
        break;
      case "clarify":
        setQuestions(e.questions);
        setAnswers(e.questions.map(() => ""));
        setPhase("clarify");
        break;
      case "plan":
        setPlan({ plan: e.plan, mode: e.mode, model: e.model });
        setPhase("done");
        break;
      case "error":
        setError({ message: e.message, retryable: e.retryable });
        setPhase("error");
        break;
      case "done":
        if (e.seconds) setDoneSeconds(e.seconds);
        if (e.llm_calls !== undefined) setLlmStats({ calls: e.llm_calls, tokens: e.tokens ?? 0 });
        break;
    }
  };

  const note = (message: string) => setTrace((t) => [...t, { kind: "status", id: `status-note-${t.length}`, message, tone: "info" }]);

  const start = async (req: PlanRequest) => {
    abort.current?.abort();
    const ctrl = new AbortController();
    abort.current = ctrl;
    lastRequest.current = req;
    setPhase("running");
    setTrace([]);
    setPrefs(null);
    setPlan(null);
    setError(null);
    setQuestions([]);
    setDoneSeconds(null);
    setLlmStats(null);
    requestAnimationFrame(() => resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
    const t0 = Date.now();
    setStartedAt(t0);

    let seen = 0;
    let sawEnd = false;
    const onEvent = (e: AgentEvent) => {
      seen += 1;
      if (e.type === "plan" || e.type === "clarify" || e.type === "error") sawEnd = true;
      handle(e);
    };
    const runId = crypto.randomUUID();
    let failure: string | null = null;
    try {
      // 1. Live stream (normal path).
      try {
        await streamPlan({ ...req, run_id: runId }, onEvent, ctrl.signal);
      } catch (err) {
        if (!(err instanceof StreamStalled || err instanceof TypeError)) throw err; // e.g. 429/400: don't retry
      }
      // 2. The stream stalled or dropped in transit: the server keeps every run, so collect the rest of it.
      if (!sawEnd && !ctrl.signal.aborted) {
        note("Live updates stalled in transit. Collecting the rest of the run from the server…");
        const events = await fetchRunResult(runId, ctrl.signal);
        if (events) {
          events.slice(seen).forEach(onEvent);
        } else {
          // 3. A different server instance answered: run once more without live updates.
          note("That server instance is gone, so I'm planning once more without live updates (about 30–60 s)…");
          setTrace((t) => t.filter((i) => i.kind === "status"));
          seen = 0;
          (await runPlanJson({ ...req, run_id: crypto.randomUUID() }, ctrl.signal)).forEach(onEvent);
        }
      }
      if (!sawEnd) failure = "The planner didn't return a result.";
    } catch (err) {
      if (!ctrl.signal.aborted) failure = err instanceof Error ? err.message : "Network error";
    } finally {
      setDoneSeconds((s) => s ?? Math.round((Date.now() - t0) / 100) / 10);
    }
    if (failure && !sawEnd && !ctrl.signal.aborted) {
      setError({ message: `${failure} Please try again.`, retryable: true });
      setPhase("error");
    }
  };

  const submitAnswers = (e: React.FormEvent) => {
    e.preventDefault();
    const base = lastRequest.current ?? {};
    start({
      ...base,
      clarifications: [
        ...(base.clarifications ?? []),
        ...questions.map((q, i) => ({ question: q, answer: answers[i]?.trim() || "no preference" })),
      ],
    });
  };

  const reset = () => {
    abort.current?.abort();
    setPhase("idle");
    setTrace([]);
    setPlan(null);
    setPrefs(null);
    setError(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const running = phase === "running";

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:py-10">
      <header className="mb-6 sm:mb-8">
        <h1 className="text-2xl font-bold tracking-tight text-ink sm:text-3xl">☀️ Perfect Saturday Planner</h1>
        <p className="mt-1 max-w-2xl text-sm text-muted">
          Tell me your city, budget, time, mood and constraints. An AI agent checks the weather, finds real places nearby,
          checks the plan is doable, and explains why each stop suits you.
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-[380px_minmax(0,1fr)]">
        <div className="lg:sticky lg:top-6 lg:self-start">
          <InputPanel busy={running} onSubmit={start} />
        </div>

        <div ref={resultsRef} className="min-w-0 scroll-mt-4 space-y-4">
          {phase === "idle" && <EmptyState />}

          {prefs && phase !== "idle" && <PrefsSummary prefs={prefs} />}

          {phase === "clarify" && (
            <form onSubmit={submitAnswers} className="rounded-2xl border border-accent/40 bg-accent-soft p-4 sm:p-5">
              <h2 className="text-sm font-semibold text-ink">A couple of quick questions first</h2>
              <p className="mt-0.5 text-xs text-muted">Your request was a bit open-ended, so here’s what would help me plan something good.</p>
              <div className="mt-3 space-y-3">
                {questions.map((q, i) => (
                  <label key={q} className="block">
                    <span className="mb-1 block text-sm text-ink">{q}</span>
                    <input
                      autoFocus={i === 0}
                      value={answers[i] ?? ""}
                      onChange={(ev) => setAnswers((a) => a.map((x, j) => (j === i ? ev.target.value : x)))}
                      className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
                    />
                  </label>
                ))}
              </div>
              <button type="submit" className="mt-4 rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-white hover:opacity-90">
                Continue planning
              </button>
            </form>
          )}

          {phase === "error" && error && (
            <div className="rounded-2xl border border-err/30 bg-err-soft p-4">
              <p className="text-sm font-medium text-err">{error.message}</p>
              {error.retryable && (
                <button type="button" onClick={() => lastRequest.current && start(lastRequest.current)} className="mt-3 rounded-lg bg-err px-3 py-1.5 text-sm font-medium text-white hover:opacity-90">
                  Try again
                </button>
              )}
            </div>
          )}

          {plan && <PlanView plan={plan.plan} mode={plan.mode} model={plan.model} onReset={reset} />}

          {phase !== "idle" && <TracePanel items={trace} running={running} startedAt={startedAt} doneSeconds={doneSeconds} llmStats={llmStats} />}
        </div>
      </div>

      <footer className="mt-10 text-center text-xs text-muted">
        Built with Gemini, OpenStreetMap (Overpass + Photon) and Open-Meteo. Place data © OpenStreetMap contributors.
      </footer>
    </div>
  );
}

function PrefsSummary({ prefs }: { prefs: Preferences }) {
  const chips = [
    `📍 ${prefs.city ?? "?"}${prefs.area ? ` · ${prefs.area}` : ""}`,
    `⏱️ ${prefs.start_time} · ${Math.round((prefs.available_minutes / 60) * 10) / 10}h`,
    `💰 ${Math.round(prefs.budget)} ${prefs.currency}`,
    `🔋 ${prefs.energy} energy`,
    ...prefs.interests.map((i) => `♥ ${i}`),
    ...prefs.hard_constraints.map((c) => `🚫 ${c}`),
    ...prefs.soft_constraints.map((c) => `~ ${c}`),
  ];
  return (
    <div className="rounded-2xl border border-line bg-surface p-3">
      <div className="mb-1.5 text-xs font-medium text-muted">What I understood</div>
      <div className="flex flex-wrap gap-1.5">
        {chips.map((c) => (
          <span key={c} className="rounded-full bg-surface-2 px-2 py-0.5 text-xs text-ink">{c}</span>
        ))}
      </div>
      {prefs.assumptions.length > 0 && <p className="mt-2 text-[11px] text-muted">Assumed: {prefs.assumptions.join(" ")}</p>}
    </div>
  );
}

function EmptyState() {
  const steps = [
    ["🧭", "Understands you", "Turns your form or free text into preferences, and asks if it's too vague."],
    ["⛅", "Checks the weather", "Saturday's real forecast, so rain means indoor picks and a plan B."],
    ["🎟️", "Finds real places", "Live OpenStreetMap data near your neighbourhood, with fallbacks."],
    ["🔎", "Checks the plan", "Time, budget, travel, opening hours, diet and crowds, then fixes problems."],
  ];
  return (
    <div className="rounded-2xl border border-dashed border-line p-5 sm:p-6">
      <h2 className="text-sm font-semibold text-ink">How it works</h2>
      <ul className="mt-3 grid gap-3 sm:grid-cols-2">
        {steps.map(([icon, title, body]) => (
          <li key={title} className="flex gap-3">
            <span className="text-xl">{icon}</span>
            <span>
              <span className="block text-sm font-medium text-ink">{title}</span>
              <span className="block text-xs text-muted">{body}</span>
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-4 text-xs text-muted">Pick an example on the left, or fill in your own, then press <b>Plan my Saturday</b>. You’ll see each step live.</p>
    </div>
  );
}
