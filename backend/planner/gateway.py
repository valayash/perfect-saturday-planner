"""Vercel AI Gateway: one OpenAI-compatible endpoint in front of many model providers.

On Vercel the project's OIDC token authenticates requests (no API key to manage).
Locally, AI_GATEWAY_API_KEY or VERCEL_OIDC_TOKEN (from `vercel env pull`) works too.
"""

from __future__ import annotations

import os

import openai
from openai import AsyncOpenAI

from .tools import TOOL_SPECS

GATEWAY_URL = "https://ai-gateway.vercel.sh/v1"


def _models(env: str, default: str) -> list[str]:
    return [m.strip() for m in os.getenv(env, default).split(",") if m.strip()]


# Defaults are all on AI Gateway's free tier, support tool calling and JSON-schema output, and come from different families.
GATEWAY_MODELS = _models("AI_GATEWAY_MODELS", "openai/gpt-oss-120b,google/gemini-2.5-flash-lite,alibaba/qwen3.7-flash")
# Parsing is a small extraction job. Gemini 2.5 Flash-Lite answered in ~1.2-2.3 s every time in testing;
# gpt-oss-20b varied from 3 to 13 s depending on which provider served it.
PARSE_MODELS = _models("AI_GATEWAY_PARSE_MODELS", "google/gemini-2.5-flash-lite,openai/gpt-oss-120b,openai/gpt-oss-20b")
# Open-weights models run on several providers; Cerebras and Groq serve them at hundreds of tokens per second.
FAST_PROVIDERS = _models("AI_GATEWAY_FAST_PROVIDERS", "cerebras,groq")

OPENAI_TOOLS = [
    {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
    for t in TOOL_SPECS
]


def request_options(model: str) -> dict:
    """Per-model extras: prefer fast providers and low reasoning effort for the gpt-oss family."""
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low", "extra_body": {"providerOptions": {"gateway": {"order": FAST_PROVIDERS}}}}
    return {}


def gateway_client(oidc_token: str | None) -> AsyncOpenAI | None:
    key = os.getenv("AI_GATEWAY_API_KEY") or oidc_token or os.getenv("VERCEL_OIDC_TOKEN")
    if not key:
        return None
    return AsyncOpenAI(api_key=key, base_url=GATEWAY_URL, timeout=20.0, max_retries=0)


def is_busy(err: Exception) -> bool:
    """Worth retrying or trying another model: rate limits, server errors, timeouts, network."""
    if isinstance(err, (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError, openai.InternalServerError)):
        return True
    return isinstance(err, openai.APIStatusError) and err.status_code >= 500


def is_account_problem(err: Exception) -> bool:
    """Bad credentials or no card on file: no gateway model will work. (A 403 for one model just skips that model.)"""
    if isinstance(err, openai.AuthenticationError):
        return True
    return isinstance(err, openai.PermissionDeniedError) and "credit card" in error_text(err).lower()


def error_text(err: Exception) -> str:
    if isinstance(err, openai.APIStatusError):
        body = err.body if isinstance(err.body, dict) else {}
        msg = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else body.get("message")
        return f"{err.status_code}: {(msg or str(err))[:160]}"
    return type(err).__name__
