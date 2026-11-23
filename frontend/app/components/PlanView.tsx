"use client";

import { useState } from "react";
import type { Leg, Plan, PlanStop } from "../lib/types";

const CATEGORY_ICON: Record<string, string> = {
  park: "🌳", garden: "🌷", lake: "🌊", viewpoint: "🌅", museum: "🏛️", gallery: "🖼️", arts_centre: "🎭",
  live_music: "🎶", theatre: "🎭", cinema: "🎬", bookstore: "📚", library: "📖", market: "🛍️", mall: "🛍️",
  historic: "🏰", bowling: "🎳", arcade: "🕹️", escape_room: "🗝️", sports: "🏓", spa: "💆",
  cafe: "☕", restaurant: "🍛", fast_food: "🥙", dessert: "🍨", street_food: "🌮",
};

function money(n: number, currency: string) {
  if (currency === "INR") return `₹${Math.round(n).toLocaleString("en-IN")}`;
  return `${Math.round(n).toLocaleString()} ${currency === "local" ? "" : currency}`.trim();
}

function Pill({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "ok" | "warn" | "accent" }) {
  const cls = {
    neutral: "bg-surface-2 text-muted",
    ok: "bg-ok-soft text-ok",
    warn: "bg-warn-soft text-warn",
    accent: "bg-accent-soft text-accent",
  }[tone];
  return <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${cls}`}>{children}</span>;
}

function LegRow({ leg, currency }: { leg: Leg; currency: string }) {
  const icon = leg.mode === "walk" ? "🚶" : "🛺";
  return (
    <div className="ml-[3.25rem] flex items-center gap-2 py-1.5 text-xs text-muted sm:ml-[4.5rem]">
      <span className="h-4 border-l-2 border-dashed border-line" />
      <span>
        {icon} {leg.minutes} min {leg.mode === "walk" ? "walk" : `by ${leg.mode}`} · {leg.km} km
        {leg.cost > 0 && ` · ~${money(leg.cost, currency)}`}
      </span>
    </div>
  );
}

function StopCard({ stop, currency }: { stop: PlanStop; currency: string }) {
  return (
    <div className="flex gap-3">
      <div className="w-10 shrink-0 pt-3 text-right sm:w-14">
        <div className="text-sm font-semibold text-ink">{stop.start}</div>
        <div className="text-[11px] text-muted">{stop.end}</div>
      </div>
      <div className="min-w-0 flex-1 rounded-xl border border-line bg-surface p-3 shadow-sm sm:p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <a href={stop.map_url} target="_blank" rel="noreferrer" className="text-base font-semibold text-ink hover:text-accent hover:underline">
              {CATEGORY_ICON[stop.category] ?? "📍"} {stop.name}
            </a>
            {stop.address && <div className="truncate text-xs text-muted">{stop.address}</div>}
          </div>
          <div className="shrink-0 text-right">
            <div className="text-sm font-semibold text-ink">{stop.est_cost > 0 ? money(stop.est_cost, currency) : "Free"}</div>
            <div className="text-[10px] text-muted">est.</div>
          </div>
        </div>
        <p className="mt-2 text-sm text-ink">{stop.activity}</p>
        <div className="mt-2 rounded-lg bg-accent-soft px-3 py-2 text-sm text-ink">
          <span className="font-semibold text-accent">Why it fits: </span>
          {stop.why_it_fits}
        </div>
        {stop.tip && <p className="mt-2 text-xs text-muted">💡 {stop.tip}</p>}
        <div className="mt-2 flex flex-wrap gap-1.5">
          <Pill>{stop.category.replace("_", " ")}</Pill>
          <Pill>{stop.indoor ? "indoor" : "outdoor"}</Pill>
          <Pill tone={stop.crowd === "high" ? "warn" : stop.crowd === "low" ? "ok" : "neutral"}>{stop.crowd} crowd (est.)</Pill>
          {stop.veg && stop.veg !== "no" && (
            <Pill tone={stop.veg === "unverified" ? "warn" : "ok"}>{stop.veg === "unverified" ? "veg: confirm on menu" : stop.veg === "only" ? "pure veg" : "veg options"}</Pill>
          )}
          {stop.opening_hours && <Pill>🕒 {stop.opening_hours}</Pill>}
          {stop.source === "curated" && <Pill tone="warn">curated data</Pill>}
        </div>
      </div>
    </div>
  );
}

function Meter({ label, value, max, display }: { label: string; value: number; max: number; display: string }) {
  const pct = Math.min(100, Math.round((value / Math.max(max, 1)) * 100));
  const over = value > max;
  return (
    <div className="rounded-xl border border-line bg-surface p-3">
      <div className="flex items-baseline justify-between text-xs text-muted">
        <span>{label}</span>
        <span className={over ? "font-semibold text-warn" : ""}>{pct}%{over ? " (over)" : ""}</span>
      </div>
      <div className="mt-1 text-sm font-semibold text-ink">{display}</div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-2">
        <div className={`h-full rounded-full ${over ? "bg-warn" : "bg-accent"}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default function PlanView({ plan, mode, model, onReset }: { plan: Plan; mode: "agent" | "rules"; model?: string; onReset: () => void }) {
  const [showCost, setShowCost] = useState(false);
  const t = plan.totals;
  const currency = plan.cost.currency;

  return (
    <article className="space-y-4">
      <header className="rounded-2xl border border-line bg-surface p-4 shadow-sm sm:p-5">
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          {mode === "agent" ? <Pill tone="accent">✨ Planned by the AI agent{model ? ` · ${model}` : ""}</Pill> : <Pill tone="warn">🛟 Rule-based backup plan</Pill>}
          {plan.date && <Pill>📅 Sat {plan.date}</Pill>}
          <Pill>📍 {plan.location}</Pill>
        </div>
        <h2 className="text-xl font-bold tracking-tight text-ink sm:text-2xl">{plan.title}</h2>
        <p className="mt-1.5 text-sm leading-relaxed text-muted">{plan.summary}</p>
        {plan.weather && <p className="mt-2 text-xs text-muted">⛅ {plan.weather}</p>}
      </header>

      {t && (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          <Meter label="Estimated cost" value={t.estimated_cost} max={t.budget} display={`${money(t.estimated_cost, currency)} of ${money(t.budget, currency)}`} />
          <Meter label="Time used" value={t.minutes_used} max={t.available_minutes} display={`${t.start}–${t.end} · ${Math.round((t.minutes_used / 60) * 10) / 10}h`} />
          <div className="rounded-xl border border-line bg-surface p-3">
            <div className="text-xs text-muted">Getting around</div>
            <div className="mt-1 text-sm font-semibold text-ink">{t.travel_minutes} min travel</div>
            <div className="mt-1 text-[11px] text-muted">between {plan.stops.length} stops</div>
          </div>
        </div>
      )}

      <div>
        {plan.stops.map((s, i) => (
          <div key={`${s.name}-${i}`}>
            {s.travel_from_previous && <LegRow leg={s.travel_from_previous} currency={currency} />}
            <StopCard stop={s} currency={currency} />
          </div>
        ))}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {plan.tradeoffs.length > 0 && (
          <section className="rounded-xl border border-line bg-surface p-4">
            <h3 className="mb-2 text-sm font-semibold text-ink">⚖️ Trade-offs</h3>
            <ul className="space-y-1.5 text-sm text-muted">
              {plan.tradeoffs.map((x) => <li key={x}>• {x}</li>)}
            </ul>
          </section>
        )}
        <section className="rounded-xl border border-line bg-surface p-4">
          <h3 className="mb-2 text-sm font-semibold text-ink">☔ Plan B: {plan.plan_b.trigger}</h3>
          <p className="text-sm text-muted">{plan.plan_b.suggestion}</p>
          {plan.plan_b.places.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {plan.plan_b.places.map((p) => (
                <a key={p.name} href={p.map_url} target="_blank" rel="noreferrer" className="rounded-full border border-line px-2 py-0.5 text-xs text-ink hover:border-accent hover:text-accent">
                  📍 {p.name}
                </a>
              ))}
            </div>
          )}
        </section>
      </div>

      {plan.warnings.length > 0 && (
        <section className="rounded-xl border border-warn/30 bg-warn-soft p-4">
          <h3 className="mb-1.5 text-sm font-semibold text-warn">Heads-up from the plan checker</h3>
          <ul className="space-y-1 text-xs text-ink/80">
            {plan.warnings.map((w) => <li key={w}>• {w}</li>)}
          </ul>
        </section>
      )}

      <section className="rounded-xl border border-line bg-surface p-4">
        <button type="button" onClick={() => setShowCost((s) => !s)} className="flex w-full items-center justify-between text-sm font-semibold text-ink">
          <span>💰 Cost breakdown · {money(plan.cost.total, currency)}</span>
          <span className="text-xs font-normal text-muted">{showCost ? "hide" : "show"}</span>
        </button>
        {showCost && (
          <table className="mt-2 w-full text-sm">
            <tbody className="divide-y divide-line">
              {plan.cost.stops.map((c) => (
                <tr key={c.name}><td className="py-1 text-muted">{c.name}</td><td className="py-1 text-right text-ink">{money(c.cost, currency)}</td></tr>
              ))}
              {plan.cost.travel.map((l) => (
                <tr key={`travel-${l.to}`}><td className="py-1 text-muted">Travel to {l.to} ({l.mode})</td><td className="py-1 text-right text-ink">{money(l.cost, currency)}</td></tr>
              ))}
              <tr className="font-semibold"><td className="py-1 text-ink">Total vs budget</td><td className="py-1 text-right text-ink">{money(plan.cost.total, currency)} / {money(plan.cost.budget, currency)}</td></tr>
            </tbody>
          </table>
        )}
        <p className="mt-2 text-[11px] text-muted">
          Places: OpenStreetMap{plan.data_sources.includes("curated") ? " + curated list" : ""}. Prices, crowds and travel times are estimates; check opening hours before you go.
        </p>
      </section>

      <button type="button" onClick={onReset} className="w-full rounded-xl border border-line bg-surface px-4 py-2.5 text-sm font-medium text-ink hover:border-accent/60">
        Plan another Saturday
      </button>
    </article>
  );
}
