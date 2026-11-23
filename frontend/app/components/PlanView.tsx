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

function prettyDate(iso: string | null) {
  if (!iso) return "This Saturday";
  return new Date(`${iso}T12:00:00`).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "short" });
}

function Pill({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "ok" | "warn" | "accent" | "ink" }) {
  const cls = {
    neutral: "bg-surface-2 text-muted",
    ok: "bg-ok-soft text-ok",
    warn: "bg-warn-soft text-warn",
    accent: "bg-accent-soft text-accent",
    ink: "bg-ink text-bg",
  }[tone];
  return <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${cls}`}>{children}</span>;
}

function LegRow({ leg, currency, label }: { leg: Leg; currency: string; label?: string }) {
  const how = leg.mode === "walk" ? `🚶 ${leg.minutes} min walk` : `🛺 ${leg.minutes} min by ${leg.mode}`;
  return (
    <div className="ml-[3.25rem] flex items-center gap-2 py-1.5 text-xs text-muted sm:ml-[4.5rem]">
      <span className="h-4 border-l-2 border-dashed border-line" />
      <span>
        {how}
        {label ? ` ${label}` : ""} · {leg.km} km{leg.cost > 0 ? ` · about ${money(leg.cost, currency)}` : ""}
      </span>
    </div>
  );
}

function StopCard({ stop, index, currency }: { stop: PlanStop; index: number; currency: string }) {
  const veg = stop.veg && stop.veg !== "no" ? (stop.veg === "only" ? "Pure vegetarian" : stop.veg === "yes" ? "Veg options" : "Veg: confirm on menu") : null;
  return (
    <div className="flex gap-3">
      <div className="w-10 shrink-0 pt-3 text-right sm:w-14">
        <div className="text-sm font-semibold text-ink">{stop.start}</div>
        <div className="text-[11px] text-muted">{stop.minutes} min</div>
      </div>
      <div className="min-w-0 flex-1 rounded-xl border border-line bg-surface p-3 shadow-sm sm:p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 items-start gap-2">
            <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-ink text-xs font-semibold text-bg">{index + 1}</span>
            <div className="min-w-0">
              <a href={stop.map_url} target="_blank" rel="noreferrer" className="text-base font-semibold text-ink hover:text-accent hover:underline">
                {CATEGORY_ICON[stop.category] ?? "📍"} {stop.name}
              </a>
              {stop.address && <div className="truncate text-xs text-muted">{stop.address}</div>}
            </div>
          </div>
          <div className="shrink-0 text-right">
            <div className="text-sm font-semibold text-ink">{stop.est_cost > 0 ? money(stop.est_cost, currency) : "Free"}</div>
            {stop.est_cost > 0 && <div className="text-[10px] text-muted">per person · est.</div>}
          </div>
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          <Pill tone={stop.source === "curated" ? "ink" : "neutral"}>{stop.source === "curated" ? "Curated" : "Live OSM"}</Pill>
          <Pill tone={stop.crowd === "high" ? "warn" : stop.crowd === "low" ? "ok" : "neutral"}>{stop.crowd_label}</Pill>
          <Pill>{stop.indoor ? "Indoor" : "Outdoor"}</Pill>
          {veg && <Pill tone={stop.veg === "unverified" ? "warn" : "ok"}>{veg}</Pill>}
          {stop.opening_hours && <Pill>🕒 {stop.opening_hours}</Pill>}
        </div>
        <p className="mt-2 text-sm text-ink">{stop.activity}</p>
        <div className="mt-2 rounded-lg bg-accent-soft px-3 py-2 text-sm text-ink">
          <span className="font-semibold text-accent">Why it fits: </span>
          {stop.why_it_fits}
        </div>
        {stop.note && <p className="mt-2 text-xs text-muted">ℹ️ {stop.note}</p>}
      </div>
    </div>
  );
}

function Section({ title, children, tone = "plain" }: { title: string; children: React.ReactNode; tone?: "plain" | "warn" }) {
  return (
    <section className={`rounded-xl border p-4 ${tone === "warn" ? "border-warn/30 bg-warn-soft" : "border-line bg-surface"}`}>
      <h3 className={`mb-2 text-[11px] font-semibold uppercase tracking-wider ${tone === "warn" ? "text-warn" : "text-muted"}`}>{title}</h3>
      {children}
    </section>
  );
}

function Bullets({ items }: { items: string[] }) {
  return (
    <ul className="space-y-1.5 text-sm text-ink/85">
      {items.map((x) => <li key={x}>• {x}</li>)}
    </ul>
  );
}

export default function PlanView({ plan, mode, model, onReset }: { plan: Plan; mode: "agent" | "rules"; model?: string; onReset: () => void }) {
  const [showCost, setShowCost] = useState(false);
  const t = plan.totals;
  const c = plan.cost;
  const currency = c.currency;
  const left = c.budget - c.total;
  const pct = Math.min(100, Math.round((c.total / Math.max(c.budget, 1)) * 100));
  const tips = plan.stops.map((s) => s.tip).filter((x): x is string => !!x);
  const weatherShort = plan.weather?.split(";").slice(0, 2).join(";");

  return (
    <article className="space-y-4">
      <header className="rounded-2xl border border-line bg-surface p-4 shadow-sm sm:p-5">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-muted">
          {prettyDate(plan.date)} · {plan.location.split(",")[0]}
        </div>
        <h2 className="mt-1 text-xl font-bold tracking-tight text-ink sm:text-2xl">{plan.title}</h2>
        <p className="mt-1.5 text-sm leading-relaxed text-muted">{plan.summary}</p>

        <div className="mt-3 flex flex-wrap gap-1.5">
          {t && <Pill>🕒 {t.start}–{t.end}</Pill>}
          {weatherShort && <Pill>⛅ {weatherShort}{plan.rain_window ? ` · rain likely ${plan.rain_window}` : ""}</Pill>}
          <Pill>🔋 {plan.energy} energy</Pill>
          {t && <Pill>🏠 home around {t.home_at}</Pill>}
        </div>

        <div className="mt-4 rounded-xl bg-surface-2 p-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div className="text-sm text-ink">
              Estimated total <span className="text-lg font-bold">{money(c.total, currency)}</span>{" "}
              <span className="text-muted">of {money(c.budget, currency)}</span>
            </div>
            <div className={`text-sm font-semibold ${left < 0 ? "text-warn" : "text-ok"}`}>
              {left >= 0 ? `${money(left, currency)} left` : `${money(-left, currency)} over`}
            </div>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-line">
            <div className={`h-full rounded-full ${left < 0 ? "bg-warn" : "bg-accent"}`} style={{ width: `${pct}%` }} />
          </div>
          <p className="mt-2 text-[11px] text-muted">
            Places {money(c.places_total, currency)} · travel {money(c.travel_total, currency)} including the ride home · 5% buffer {money(c.buffer, currency)}.
            Prices for live places are category estimates.
          </p>
        </div>
      </header>

      <div>
        {plan.stops.map((s, i) => (
          <div key={`${s.name}-${i}`}>
            {s.travel_from_previous && (
              <LegRow leg={s.travel_from_previous} currency={currency} label={s.travel_from_previous.from_start ? `from ${s.travel_from_previous.from ?? plan.start_label}` : undefined} />
            )}
            <StopCard stop={s} index={i} currency={currency} />
          </div>
        ))}
        {plan.home_leg && t && (
          <div className="ml-[3.25rem] flex items-center gap-2 py-2 text-xs text-muted sm:ml-[4.5rem]">
            <span className="h-4 border-l-2 border-dashed border-line" />
            <span>
              🏠 ~{plan.home_leg.minutes} min back to {plan.start_label}
              {plan.home_leg.cost > 0 ? ` · about ${money(plan.home_leg.cost, currency)}` : ""} · home around {t.home_at}
            </span>
          </div>
        )}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Section title={`Plan B · ${plan.plan_b.trigger}`}>
          <p className="text-sm text-ink/85">{plan.plan_b.suggestion}</p>
          {plan.plan_b.places.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {plan.plan_b.places.map((p) => (
                <a key={p.name} href={p.map_url} target="_blank" rel="noreferrer" className="rounded-full border border-line px-2 py-0.5 text-xs text-ink hover:border-accent hover:text-accent">
                  📍 {p.name}
                </a>
              ))}
            </div>
          )}
        </Section>
        {plan.worth_knowing.length > 0 && (
          <Section title="Worth knowing" tone="warn">
            <Bullets items={plan.worth_knowing} />
          </Section>
        )}
        {plan.tradeoffs.length > 0 && (
          <Section title="Trade-offs">
            <Bullets items={plan.tradeoffs} />
          </Section>
        )}
        {tips.length > 0 && (
          <Section title="Tips">
            <Bullets items={tips} />
          </Section>
        )}
        {plan.assumptions.length > 0 && (
          <Section title="Assumptions">
            <Bullets items={plan.assumptions} />
          </Section>
        )}
      </div>

      <section className="rounded-xl border border-line bg-surface p-4">
        <button type="button" onClick={() => setShowCost((x) => !x)} className="flex w-full items-center justify-between text-sm font-semibold text-ink">
          <span>💰 Cost breakdown · {money(c.total, currency)}</span>
          <span className="text-xs font-normal text-muted">{showCost ? "hide" : "show"}</span>
        </button>
        {showCost && (
          <table className="mt-2 w-full text-sm">
            <tbody className="divide-y divide-line">
              {c.stops.map((x) => (
                <tr key={x.name}><td className="py-1 text-muted">{x.name}</td><td className="py-1 text-right text-ink">{money(x.cost, currency)}</td></tr>
              ))}
              {c.travel.map((l, i) => (
                <tr key={`travel-${i}`}>
                  <td className="py-1 text-muted">{l.from ? `${l.from} → ` : "→ "}{l.to} ({l.mode === "walk" ? "walk" : l.mode}, {l.km} km)</td>
                  <td className="py-1 text-right text-ink">{money(l.cost, currency)}</td>
                </tr>
              ))}
              <tr><td className="py-1 text-muted">5% buffer</td><td className="py-1 text-right text-ink">{money(c.buffer, currency)}</td></tr>
              <tr className="font-semibold"><td className="py-1 text-ink">Total vs budget</td><td className="py-1 text-right text-ink">{money(c.total, currency)} / {money(c.budget, currency)}</td></tr>
            </tbody>
          </table>
        )}
      </section>

      <div className="flex flex-col gap-2 sm:flex-row">
        <a href={plan.route_url} target="_blank" rel="noreferrer" className="flex-1 rounded-xl bg-ink px-4 py-3 text-center text-sm font-semibold text-bg hover:opacity-90">
          Open route in Google Maps ↗
        </a>
        <button type="button" onClick={onReset} className="flex-1 rounded-xl border border-line bg-surface px-4 py-3 text-sm font-medium text-ink hover:border-accent/60">
          Plan another Saturday
        </button>
      </div>

      <p className="text-center text-[11px] text-muted">
        {mode === "agent" ? `Planned by ${model ?? "the AI agent"}` : "Rule-based backup plan (AI unavailable)"}
        {plan.stats ? ` · ${plan.stats.llm_calls} LLM calls · ${plan.stats.tokens.toLocaleString()} tokens · ${plan.stats.seconds}s` : ""}
        {` · ${plan.validator_issues} validator issue${plan.validator_issues === 1 ? "" : "s"}`}
        {" · places: OpenStreetMap"}{plan.data_sources.includes("curated") ? " + curated list" : ""}
      </p>
    </article>
  );
}
