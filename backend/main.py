"""FastAPI app: POST /api/plan streams the agent's trace and final plan as Server-Sent Events."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict, deque
from pathlib import Path

# Local dev convenience: load backend/.env (never committed; on Vercel use project env vars).
_env_file = Path(__file__).with_name(".env")
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip("\"'"))

from fastapi import FastAPI, Request  # noqa: E402
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


@app.post("/api/plan")
async def plan(req: PlanRequest, request: Request):
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
    if _rate_limited(ip):
        return JSONResponse({"error": "Too many plans in a short time. Try again in a few minutes."}, status_code=429)
    if not (req.city or req.free_text):
        return JSONResponse({"error": "Tell me at least your city, or describe your Saturday in your own words."}, status_code=400)

    async def stream():
        queue: asyncio.Queue = asyncio.Queue()

        async def produce():
            try:
                async for event in run(req, oidc_token=request.headers.get("x-vercel-oidc-token")):
                    await queue.put(event)
            except Exception:
                log.exception("agent crashed")
                await queue.put({"type": "error", "message": "Something went wrong on our side. Please try again.", "retryable": True})
            finally:
                await queue.put(None)

        task = asyncio.create_task(produce())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=5)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"  # stops proxies from closing an idle stream
                    continue
                if event is None:
                    break
                yield _sse(event)
        finally:
            task.cancel()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
