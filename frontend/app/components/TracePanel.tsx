"use client";

import { useEffect, useState } from "react";
import type { StepItem, TraceItem } from "../lib/types";

const ICONS: Record<string, string> = {
  parse_preferences: "🧭",
  resolve_location: "📍",
  get_weather: "⛅",
  find_activities: "🎟️",
  find_food: "🍽️",
  estimate_cost: "💰",
  validate_plan: "🔎",
  submit_plan: "📝",
};

const SOURCE_STYLE: Record<string, string> = {
  live: "bg-ok-soft text-ok",
  gemini: "bg-accent-soft text-accent",
  gateway: "bg-accent-soft text-accent",
  fallback: "bg-warn-soft text-warn",
  generic: "bg-warn-soft text-warn",
  rules: "bg-warn-soft text-warn",
  unavailable: "bg-err-soft text-err",
};

function Badge({ source }: { source?: string | null }) {
  if (!source) return null;
  return <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${SOURCE_STYLE[source] ?? "bg-surface-2 text-muted"}`}>{source}</span>;
}

function Step({ item }: { item: StepItem }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded-lg border border-line bg-surface">
      <button type="button" onClick={() => setOpen((o) => !o)} className="flex w-full items-start gap-2.5 px-3 py-2 text-left">
        <span className="mt-0.5 text-base leading-none">{ICONS[item.name] ?? "🔧"}</span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-1.5">
            <span className="text-sm font-medium text-ink">{item.label}</span>
            <code className="font-mono text-[11px] text-muted">{item.name}</code>
            <Badge source={item.source} />
          </span>
          {item.summary && <span className={`mt-0.5 block text-xs ${item.status === "error" ? "text-err" : "text-muted"}`}>{item.summary}</span>}
        </span>
        <span className="mt-0.5 shrink-0 text-xs text-muted">
          {item.status === "running" ? (
            <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          ) : (
            <span className="flex items-center gap-1">
              {item.ms !== undefined && <span>{item.ms < 1000 ? `${item.ms}ms` : `${(item.ms / 1000).toFixed(1)}s`}</span>}
              <span className={item.status === "error" ? "text-err" : "text-ok"}>{item.status === "error" ? "✕" : "✓"}</span>
            </span>
          )}
        </span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-line px-3 py-2">
          <Json label="input" value={item.input} />
          {item.output !== undefined && <Json label="output" value={item.output} />}
        </div>
      )}
    </li>
  );
}

function Json({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted">{label}</div>
      <pre className="max-h-64 overflow-auto rounded-md bg-surface-2 p-2 font-mono text-[11px] leading-snug text-ink">{JSON.stringify(value ?? {}, null, 2)}</pre>
    </div>
  );
}

function Thought({ text, live }: { text: string; live: boolean }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 280;
  return (
    <li className="flex gap-2.5 px-1">
      <span className="mt-0.5 text-base leading-none">💭</span>
      <div className="min-w-0 flex-1 text-xs italic leading-relaxed text-muted">
        <span className="whitespace-pre-wrap">{open || !long ? text : `…${text.slice(-280)}`}</span>
        {live && <span className="thinking-dot ml-1 not-italic">●</span>}
        {long && !live && (
          <button type="button" onClick={() => setOpen((o) => !o)} className="ml-1 not-italic text-accent hover:underline">
            {open ? "less" : "more"}
          </button>
        )}
      </div>
    </li>
  );
}

export default function TracePanel({
  items,
  running,
  startedAt,
  doneSeconds,
}: {
  items: TraceItem[];
  running: boolean;
  startedAt: number | null;
  doneSeconds: number | null;
}) {
  const [now, setNow] = useState(() => Date.now());
  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(t);
  }, [running]);

  const tools = items.filter((i): i is StepItem => i.kind === "step");
  const elapsed = running && startedAt ? Math.max(0, (now - startedAt) / 1000) : doneSeconds;
  const lastThought = [...items].reverse().find((i) => i.kind === "thought");

  return (
    <section className="rounded-2xl border border-line bg-surface-2/60 p-3 sm:p-4">
      <button type="button" onClick={() => setCollapsed((c) => !c)} className="mb-2 flex w-full items-center justify-between text-left">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
          Agent trace
          {running && <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-accent" />}
        </h2>
        <span className="text-xs text-muted">
          {tools.length} step{tools.length === 1 ? "" : "s"}
          {elapsed != null ? ` · ${elapsed.toFixed(1)}s` : ""} · {collapsed ? "show" : "hide"}
        </span>
      </button>
      {!collapsed && (
        <ol className="space-y-1.5">
          {items.map((item) =>
            item.kind === "step" ? (
              <Step key={item.id} item={item} />
            ) : item.kind === "thought" ? (
              <Thought key={item.id} text={item.text} live={running && item === lastThought} />
            ) : (
              <li key={item.id} className={`rounded-lg px-3 py-2 text-xs ${item.tone === "info" ? "bg-surface text-muted" : "bg-warn-soft text-warn"}`}>
                {item.tone === "info" ? "ℹ️" : "⚠️"} {item.message}
              </li>
            ),
          )}
          {running && items.length === 0 && <li className="px-1 text-xs text-muted">Starting…</li>}
        </ol>
      )}
    </section>
  );
}
