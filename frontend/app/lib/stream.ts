import type { AgentEvent, PlanRequest } from "./types";

/** The server sends a keep-alive every 3 s, so this much silence means the connection is stuck. */
const STALL_MS = 12_000;

export class StreamStalled extends Error {}

function postPlan(payload: PlanRequest, signal: AbortSignal, query = ""): Promise<Response> {
  return fetch(`/api/plan${query}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
}

/** The API's own error message (e.g. the rate limit), or a generic one. */
async function errorMessage(res: Response): Promise<string> {
  const fallback = `Request failed (${res.status})`;
  try {
    const body = await res.json();
    return body.error ?? (typeof body.detail === "string" ? body.detail : fallback);
  } catch {
    return fallback;
  }
}

/** POST the request and call onEvent for every Server-Sent Event the agent streams back. */
export async function streamPlan(
  payload: PlanRequest,
  onEvent: (event: AgentEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await postPlan(payload, signal);
  if (!res.ok || !res.body) throw new Error(await errorMessage(res));

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let lastByte = Date.now();
  let stalled = false;
  const watchdog = setInterval(() => {
    if (Date.now() - lastByte > STALL_MS) {
      stalled = true;
      reader.cancel().catch(() => {});
    }
  }, 2_000);
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      lastByte = Date.now();
      buffer += decoder.decode(value, { stream: true });
      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const chunk = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        for (const line of chunk.split("\n")) {
          if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)) as AgentEvent);
        }
      }
    }
  } finally {
    clearInterval(watchdog);
  }
  if (stalled) throw new StreamStalled("The connection to the planner stalled.");
}

/** Long-poll a run's stored events from `after` on. null if another server instance ran it. */
export async function pollRun(runId: string, after: number, signal: AbortSignal): Promise<{ events: AgentEvent[]; done: boolean } | null> {
  const res = await fetch(`/api/plan/${encodeURIComponent(runId)}?after=${after}&wait=8`, { signal });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Couldn't collect the result (${res.status})`);
  return (await res.json()) as { events: AgentEvent[]; done: boolean };
}

/** Non-streaming fallback: run the whole plan and get every event in one ordinary response. */
export async function runPlanJson(payload: PlanRequest, signal: AbortSignal): Promise<AgentEvent[]> {
  const res = await postPlan(payload, signal, "?mode=json");
  if (!res.ok) throw new Error(await errorMessage(res));
  return ((await res.json()) as { events: AgentEvent[] }).events;
}
