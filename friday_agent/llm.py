"""Thin async client for Ollama's /api/chat (streaming, tool calling)."""
from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

import httpx

from .config import FALLBACK_MODELS, OLLAMA_HOST, OLLAMA_KEEP_ALIVE, OLLAMA_TIMEOUT_SECONDS

EMBEDDING_HINTS = ("embed", "bge-", "minilm", "e5-")
_MODEL_CACHE: dict[str, Any] = {"at": 0.0, "models": None}
MODEL_CACHE_SECONDS = 15.0

class LLMError(Exception):
    pass

async def chat_stream(
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    options: dict | None = None,
) -> AsyncIterator[dict]:
    """Yield raw Ollama chunks. Each chunk: {"message": {...}, "done": bool, ...}."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "keep_alive": OLLAMA_KEEP_ALIVE,
    }
    if tools:
        payload["tools"] = tools
    if options:
        payload["options"] = options
    try:
        timeout = httpx.Timeout(OLLAMA_TIMEOUT_SECONDS, connect=5)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", f"{OLLAMA_HOST}/api/chat", json=payload) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode("utf-8", "replace")
                    try:
                        msg = json.loads(body).get("error", body)
                    except Exception:
                        msg = body
                    if "not found" in msg:
                        msg += f"  ->  run: ollama pull {model}"
                    if "does not support tools" in msg:
                        msg += "  ->  choose a tool-capable model, e.g. qwen2.5-coder:7b"
                    raise LLMError(msg)
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):  # Ollama reports some failures inside the stream
                        raise LLMError(f"Ollama error: {chunk['error']}")
                    yield chunk
    except httpx.ConnectError as e:
        raise LLMError(f"Cannot reach Ollama at {OLLAMA_HOST}. Start it with: ollama serve") from e
    except httpx.TimeoutException as e:
        raise LLMError(f"Ollama did not respond within {int(OLLAMA_TIMEOUT_SECONDS)}s "
                       "(model may still be loading; raise FRIDAY_OLLAMA_TIMEOUT).") from e
    except (httpx.RemoteProtocolError, httpx.ReadError) as e:
        raise LLMError("The connection to Ollama was lost mid-response (was Ollama stopped?).") from e

async def warm_up(model: str, prefix: list[dict], options: dict | None = None) -> dict[str, Any] | None:
    """Load ``model`` and evaluate ``prefix`` (usually the system prompt) into Ollama's prompt cache.

    The next request that starts with the same messages and options reuses the cached tokens,
    so the first question does not pay for model loading or the system prompt. One generated
    token, no tools. Returns Ollama's timing counters, or None if Ollama is unreachable.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": [*prefix, {"role": "user", "content": "Reply with OK."}],
        "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {**(options or {}), "num_predict": 1},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(OLLAMA_TIMEOUT_SECONDS, connect=5)) as client:
            response = await client.post(f"{OLLAMA_HOST}/api/chat", json=payload)
            if response.status_code >= 400:
                return None
            data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    return {key: data.get(key) for key in ("load_duration", "prompt_eval_count", "prompt_eval_duration",
                                           "total_duration")}


async def installed_models(force: bool = False) -> list[str] | None:
    """Installed Ollama models (cached briefly), or None when Ollama is unreachable."""
    now = time.monotonic()
    if not force and _MODEL_CACHE["models"] is not None and now - _MODEL_CACHE["at"] < MODEL_CACHE_SECONDS:
        return _MODEL_CACHE["models"]
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{OLLAMA_HOST}/api/tags")
            r.raise_for_status()
            models = sorted(m["name"] for m in r.json().get("models", []))
    except Exception:
        _MODEL_CACHE.update(at=now, models=None)
        return None
    _MODEL_CACHE.update(at=now, models=models)
    return models


async def list_models() -> list[str]:
    return await installed_models() or []


def _same_model(a: str, b: str) -> bool:
    norm = lambda name: name if ":" in name else f"{name}:latest"  # noqa: E731
    return norm(a) == norm(b)


async def resolve_model(requested: str, *, purpose: str = "chat") -> tuple[str, str | None]:
    """Return (model to use, note). Falls back when the requested model is not installed.

    Raises LLMError when Ollama is unreachable or no usable model exists.
    """
    models = await installed_models()
    if models is None:
        raise LLMError(f"Cannot reach Ollama at {OLLAMA_HOST}. Start it with: ollama serve")
    if any(_same_model(requested, m) for m in models):
        return requested, None
    candidates = [m for m in models if not any(h in m.lower() for h in EMBEDDING_HINTS)]
    for wanted in FALLBACK_MODELS:
        match = next((m for m in candidates if _same_model(wanted, m)), None)
        if match:
            return match, f"Model '{requested}' is not installed; using '{match}'. Run: ollama pull {requested}"
    if candidates:
        return candidates[0], f"Model '{requested}' is not installed; using '{candidates[0]}'. Run: ollama pull {requested}"
    raise LLMError(f"No {purpose} model is installed in Ollama. Run: ollama pull {requested}")

