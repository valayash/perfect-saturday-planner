export type Clarification = { question: string; answer: string };
export type ClarifyQuestion = { id: string; question: string; options: string[] };

export type PlanRequest = {
  city?: string;
  area?: string;
  budget?: number;
  available_time?: string;
  start_time?: string;
  mood?: string;
  interests?: string[];
  constraints?: string[];
  free_text?: string;
  clarifications?: Clarification[];
  run_id?: string;
};

export type Preferences = {
  city: string | null;
  area: string | null;
  budget: number;
  currency: string;
  available_minutes: number;
  start_time: string;
  mood: string;
  energy: "low" | "medium" | "high";
  interests: string[];
  hard_constraints: string[];
  soft_constraints: string[];
  assumptions: string[];
};

export type Leg = { mode: string; km: number; minutes: number; cost: number; from_start?: boolean; from?: string };

export type PlanStop = {
  start: string;
  end: string;
  minutes: number;
  name: string;
  category: string;
  activity: string;
  why_it_fits: string;
  tip: string | null;
  est_cost: number;
  address: string | null;
  indoor: boolean;
  crowd: "low" | "medium" | "high";
  crowd_label: string;
  veg: "yes" | "only" | "unverified" | "no" | null;
  opening_hours: string | null;
  source: "openstreetmap" | "curated";
  note: string | null;
  map_url: string;
  travel_from_previous: Leg | null;
};

export type Plan = {
  title: string;
  summary: string;
  date: string | null;
  location: string;
  start_label: string;
  weather: string | null;
  rain_window: string | null;
  energy: "low" | "medium" | "high";
  stops: PlanStop[];
  home_leg: Leg | null;
  totals: {
    start: string;
    end: string;
    home_at: string;
    minutes_used: number;
    available_minutes: number;
    travel_minutes: number;
    estimated_cost: number;
    budget: number;
    currency: string;
  } | null;
  cost: {
    stops: { name: string; cost: number }[];
    travel: ({ to: string; from?: string } & Leg)[];
    places_total: number;
    travel_total: number;
    buffer: number;
    total: number;
    budget: number;
    currency: string;
  };
  tradeoffs: string[];
  plan_b: { trigger: string; suggestion: string; places: { name: string; map_url: string }[] };
  worth_knowing: string[];
  assumptions: string[];
  route_url: string;
  warnings: string[];
  validator_issues: number;
  data_sources: string[];
  stats?: { llm_calls: number; tokens: number; seconds: number };
};

export type StepItem = {
  kind: "step";
  id: string;
  name: string;
  label: string;
  actor?: "llm" | "code";
  input?: unknown;
  status: "running" | "done" | "error";
  summary?: string;
  ms?: number;
  source?: string | null;
  output?: unknown;
};
export type ThoughtItem = { kind: "thought"; id: string; text: string };
export type StatusItem = { kind: "status"; id: string; message: string; tone?: "info" | "warn" };
export type TraceItem = StepItem | ThoughtItem | StatusItem;

export type AgentEvent =
  | { type: "step_start"; id: string; name: string; label: string; input?: unknown; actor?: "llm" | "code" }
  | { type: "step_end"; id: string; ok: boolean; summary: string; source?: string | null; ms: number; output?: unknown }
  | { type: "thought_start"; block: string; kind: string }
  | { type: "thought_delta"; block: string; text: string }
  | { type: "status"; message: string; tone?: "info" | "warn" }
  | { type: "preferences"; preferences: Preferences; source: string }
  | { type: "clarify"; questions: (string | ClarifyQuestion)[]; summary?: string; assumptions: string[] }
  | { type: "plan"; plan: Plan; mode: "agent" | "rules"; model?: string }
  | { type: "error"; message: string; retryable: boolean }
  | { type: "done"; seconds?: number; llm_calls?: number; tokens?: number };
