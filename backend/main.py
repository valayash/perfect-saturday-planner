"""FastAPI app: POST /api/plan streams the agent's trace and final plan as Server-Sent Events.

Every run's events are also kept in memory for a few minutes, so if a stream stalls in transit the
browser can collect the finished result with GET /api/plan/{run_id} (an ordinary JSON response).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

# Local dev convenience: load backend/.env (never committed; on Vercel use project env vars).
_env_file = Path(__file__).with_name(".env")
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip("\"'"))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse

from planner.agent import run  # noqa: E402
from planner.models import PlanRequest  # noqa: E402

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("planner")

app = FastAPI(title="Perfect Saturday Planner API")

# Light abuse protection for a public demo. Per-instance only, which is fine for this purpose.
RATE_LIMIT, RATE_WINDOW = 10, 600
_hits: dict[str, deque] = defaultdict(deque)


def _rate_limited(ip: str) -> bool:
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > RATE_WINDOW:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        return True
    q.append(now)
    return False


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


RUN_TTL_S = 900
RUN_WAIT_S = 250  # Vercel functions stop at 300 s


@dataclass
class _Run:
    events: list = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    created: float = field(default_factory=time.monotonic)
    task: asyncio.Task | None = None


_runs: dict[str, _Run] = {}


def _start_run(req: PlanRequest, oidc_token: str | None) -> _Run:
    now = time.monotonic()
    for rid in [k for k, r in _runs.items() if now - r.created > RUN_TTL_S]:
        _runs.pop(rid, None)
    r = _Run()

    async def produce():
        try:
            async for event in run(req, oidc_token=oidc_token):
                r.events.append(json.loads(json.dumps(event, default=str)))
        except Exception:
            log.exception("agent crashed")
            r.events.append({"type": "error", "message": "Something went wrong on our side. Please try again.", "retryable": True})
        finally:
            r.done.set()

    r.task = asyncio.create_task(produce())  # not tied to the HTTP stream: it finishes even if the stream stalls
    if req.run_id:
        _runs[req.run_id] = r
    return r


def _client_ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()


@app.post("/api/plan")
async def plan(req: PlanRequest, request: Request, mode: str = "stream"):
    if _rate_limited(_client_ip(request)):
        return JSONResponse({"error": "Too many plans in a short time. Try again in a few minutes."}, status_code=429)
    if not (req.city or req.free_text):
        return JSONResponse({"error": "Tell me at least your city, or describe your Saturday in your own words."}, status_code=400)
    r = _start_run(req, request.headers.get("x-vercel-oidc-token"))

    if mode == "json":  # non-streaming fallback: one ordinary response with every event
        try:
            await asyncio.wait_for(r.done.wait(), timeout=RUN_WAIT_S)
        except asyncio.TimeoutError:
            pass
        return {"events": r.events, "done": r.done.is_set()}

    async def stream():
        sent, quiet_since, t0 = 0, time.monotonic(), time.monotonic()
        try:
            while True:
                if sent < len(r.events):
                    while sent < len(r.events):
                        yield _sse(r.events[sent])
                        sent += 1
                    quiet_since = time.monotonic()
                elif r.done.is_set():
                    break
                else:
                    if time.monotonic() - quiet_since >= 3:
                        yield ": keep-alive\n\n"  # stops proxies from closing an idle stream
                        quiet_since = time.monotonic()
                    await asyncio.sleep(0.2)
        finally:
            log.info("stream closed after %.1fs: %d/%d events sent, agent_finished=%s", time.monotonic() - t0, sent, len(r.events), r.done.is_set())

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.get("/api/plan/{run_id}")
async def plan_result(run_id: str, after: int = 0, wait: float = 8.0):
    """Long-poll a run's events: returns events[after:] as soon as there are new ones (or after `wait` seconds).
    Used when a live stream stalls in transit. 404 if this server instance didn't run it."""
    r = _runs.get(run_id)
    if not r:
        raise HTTPException(status_code=404, detail="Unknown run on this server instance.")
    deadline = time.monotonic() + max(0.0, min(wait, 20.0))
    while len(r.events) <= after and not r.done.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(0.25)
    return {"events": r.events[after:], "next": len(r.events), "done": r.done.is_set()}
