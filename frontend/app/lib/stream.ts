import type { AgentEvent, PlanRequest } from "./types";

/** POST the request and call onEvent for every Server-Sent Event the agent streams back. */
export async function streamPlan(
  payload: PlanRequest,
  onEvent: (event: AgentEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!res.ok || !res.body) {
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      message = body.error ?? (typeof body.detail === "string" ? body.detail : message);
    } catch {}
    throw new Error(message);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
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
}
