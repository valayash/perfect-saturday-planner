"use client";

import { useState } from "react";
import type { PlanRequest } from "../lib/types";

const INTERESTS = ["food", "coffee", "music", "walks", "art", "books", "history", "nature", "movies", "games", "dessert", "shopping"];
const CONSTRAINTS = ["vegetarian", "vegan", "avoid crowded places", "low walking", "no alcohol", "wheelchair accessible", "kid-friendly"];
const DURATIONS = ["2 hours", "3 hours", "4 hours", "5 hours", "6 hours", "8 hours", "full day"];

type FormState = {
  city: string;
  area: string;
  budget: string;
  available_time: string;
  start_time: string;
  mood: string;
  interests: string[];
  constraints: string[];
};

const EMPTY: FormState = {
  city: "", area: "", budget: "", available_time: "4 hours", start_time: "", mood: "", interests: [], constraints: [],
};

type Preset = { label: string; hint: string; form?: Partial<FormState>; text?: string };

const PRESETS: Preset[] = [
  {
    label: "Tired in Bangalore",
    hint: "the assignment example",
    form: {
      city: "Bangalore", area: "Indiranagar", budget: "2000", available_time: "4 hours",
      mood: "tired but wants to do something fun", interests: ["food", "music", "walks"],
      constraints: ["vegetarian", "avoid crowded places"],
    },
  },
  {
    label: "₹600 student in Mumbai",
    hint: "tight budget",
    form: {
      city: "Mumbai", area: "Bandra", budget: "600", available_time: "5 hours", start_time: "12:00",
      mood: "broke but in a social mood", interests: ["art", "walks", "food"], constraints: ["vegetarian"],
    },
  },
  {
    label: "Cozy day in Delhi",
    hint: "indoor, bookish",
    form: {
      city: "Delhi", area: "Khan Market", budget: "2500", available_time: "3 hours", start_time: "16:00",
      mood: "cozy, want to stay dry and slow down", interests: ["books", "coffee", "history"], constraints: [],
    },
  },
  {
    label: "Energetic in London",
    hint: "works worldwide",
    form: {
      city: "London", area: "South Bank", budget: "60", available_time: "6 hours", start_time: "11:00",
      mood: "energetic and curious", interests: ["art", "music", "food"], constraints: [],
    },
  },
  { label: "Something vague…", hint: "asks clarifying questions", text: "plan something fun for me this saturday" },
];

function Chips({ options, selected, onToggle }: { options: string[]; selected: string[]; onToggle: (v: string) => void }) {
  const extra = selected.filter((s) => !options.includes(s));
  return (
    <div className="flex flex-wrap gap-1.5">
      {[...options, ...extra].map((opt) => {
        const on = selected.includes(opt);
        return (
          <button
            key={opt}
            type="button"
            onClick={() => onToggle(opt)}
            className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
              on ? "border-accent bg-accent text-white" : "border-line bg-surface text-ink hover:border-accent/60"
            }`}
          >
            {opt}
          </button>
        );
      })}
    </div>
  );
}

function Field({ label, children, hint, group }: { label: string; children: React.ReactNode; hint?: string; group?: boolean }) {
  // Chip groups contain buttons, so they must not be wrapped in a <label> (clicking it would press the first chip).
  const Tag = group ? "div" : "label";
  return (
    <Tag className="block">
      <span className="mb-1 flex items-baseline justify-between text-xs font-medium text-muted">
        {label}
        {hint && <span className="font-normal opacity-80">{hint}</span>}
      </span>
      {children}
    </Tag>
  );
}

const inputCls =
  "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink placeholder:text-muted/70 focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20";

export default function InputPanel({ busy, onSubmit }: { busy: boolean; onSubmit: (req: PlanRequest) => void }) {
  const [mode, setMode] = useState<"form" | "text">("form");
  const [form, setForm] = useState<FormState>({ ...EMPTY, ...PRESETS[0].form });
  const [text, setText] = useState("");
  const [custom, setCustom] = useState({ interest: "", constraint: "" });

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm((f) => ({ ...f, [key]: value }));
  const toggle = (key: "interests" | "constraints", v: string) =>
    setForm((f) => ({ ...f, [key]: f[key].includes(v) ? f[key].filter((x) => x !== v) : [...f[key], v] }));

  const applyPreset = (p: Preset) => {
    if (p.text) {
      setMode("text");
      setText(p.text);
    } else {
      setMode("form");
      setForm({ ...EMPTY, ...p.form });
    }
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (mode === "text") {
      onSubmit({ free_text: text.trim() });
      return;
    }
    onSubmit({
      city: form.city.trim() || undefined,
      area: form.area.trim() || undefined,
      budget: form.budget ? Number(form.budget) : undefined,
      available_time: form.available_time,
      start_time: form.start_time || undefined,
      mood: form.mood.trim() || undefined,
      interests: form.interests,
      constraints: form.constraints,
    });
  };

  const canSubmit = !busy && (mode === "text" ? text.trim().length > 2 : form.city.trim().length > 1);

  return (
    <form onSubmit={submit} className="rounded-2xl border border-line bg-surface p-4 shadow-sm sm:p-5">
      <div className="mb-4">
        <div className="mb-1.5 text-xs font-medium text-muted">Try an example</div>
        <div className="flex flex-wrap gap-1.5">
          {PRESETS.map((p) => (
            <button
              key={p.label}
              type="button"
              onClick={() => applyPreset(p)}
              title={p.hint}
              className="rounded-lg border border-line bg-surface-2 px-2.5 py-1 text-xs text-ink hover:border-accent/60"
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      <div className="mb-4 grid grid-cols-2 rounded-lg bg-surface-2 p-1 text-sm">
        {(["form", "text"] as const).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setMode(m)}
            className={`rounded-md py-1.5 font-medium transition-colors ${mode === m ? "bg-surface text-ink shadow-sm" : "text-muted"}`}
          >
            {m === "form" ? "Fill a form" : "Just describe it"}
          </button>
        ))}
      </div>

      {mode === "text" ? (
        <Field label="Describe your ideal Saturday">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={6}
            maxLength={1500}
            placeholder="e.g. I'm in Pune, have ₹1500 and the afternoon free, feeling lazy but want good food and maybe some live music. I'm vegetarian."
            className={inputCls}
          />
        </Field>
      ) : (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Field label="City">
              <input value={form.city} onChange={(e) => set("city", e.target.value)} placeholder="Bangalore" className={inputCls} maxLength={80} />
            </Field>
            <Field label="Neighbourhood" hint="optional">
              <input value={form.area} onChange={(e) => set("area", e.target.value)} placeholder="Indiranagar" className={inputCls} maxLength={80} />
            </Field>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <Field label="Budget">
              <input type="number" min={0} value={form.budget} onChange={(e) => set("budget", e.target.value)} placeholder="2000" className={inputCls} />
            </Field>
            <Field label="Time">
              <select value={form.available_time} onChange={(e) => set("available_time", e.target.value)} className={inputCls}>
                {DURATIONS.map((d) => <option key={d}>{d}</option>)}
              </select>
            </Field>
            <Field label="Start" hint="optional">
              <input type="time" value={form.start_time} onChange={(e) => set("start_time", e.target.value)} className={inputCls} />
            </Field>
          </div>
          <Field label="Mood">
            <input value={form.mood} onChange={(e) => set("mood", e.target.value)} placeholder="tired but wants to do something fun" className={inputCls} maxLength={200} />
          </Field>
          <Field label="Interests" group>
            <Chips options={INTERESTS} selected={form.interests} onToggle={(v) => toggle("interests", v)} />
            <AddCustom value={custom.interest} onChange={(v) => setCustom((c) => ({ ...c, interest: v }))} onAdd={(v) => toggle("interests", v)} placeholder="add your own" />
          </Field>
          <Field label="Constraints" group>
            <Chips options={CONSTRAINTS} selected={form.constraints} onToggle={(v) => toggle("constraints", v)} />
            <AddCustom value={custom.constraint} onChange={(v) => setCustom((c) => ({ ...c, constraint: v }))} onAdd={(v) => toggle("constraints", v)} placeholder="e.g. no spicy food" />
          </Field>
        </div>
      )}

      <button
        type="submit"
        disabled={!canSubmit}
        className="mt-5 w-full rounded-xl bg-accent px-4 py-3 text-sm font-semibold text-white shadow-sm transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {busy ? "Planning your Saturday…" : "Plan my Saturday"}
      </button>
    </form>
  );
}

function AddCustom({ value, onChange, onAdd, placeholder }: { value: string; onChange: (v: string) => void; onAdd: (v: string) => void; placeholder: string }) {
  const add = () => {
    const v = value.trim().toLowerCase();
    if (v) onAdd(v);
    onChange("");
  };
  return (
    <div className="mt-1.5 flex gap-1.5">
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            add();
          }
        }}
        placeholder={placeholder}
        maxLength={40}
        className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2 py-1 text-xs text-ink placeholder:text-muted/70 focus:border-accent focus:outline-none"
      />
      <button type="button" onClick={add} className="rounded-md border border-line px-2 text-xs text-muted hover:border-accent/60">
        Add
      </button>
    </div>
  );
}
