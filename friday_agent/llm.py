"""Thin async client for Ollama's /api/chat (streaming, tool calling)."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from .config import OLLAMA_HOST

class LLMError(Exception):
    pass

async def chat_stream(
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    options: dict | None = None,
) -> AsyncIterator[dict]:
    """Yield raw Ollama chunks. Each chunk: {"message": {...}, "done": bool, ...}."""
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
    if tools:
        payload["tools"] = tools
    if options:
        payload["options"] = options
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5)) as client:
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
                    if line.strip():
                        yield json.loads(line)
    except httpx.ConnectError as e:
        raise LLMError(f"Cannot reach Ollama at {OLLAMA_HOST}. Start it with: ollama serve") from e

async def list_models() -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{OLLAMA_HOST}/api/tags")
            r.raise_for_status()
            return sorted(m["name"] for m in r.json().get("models", []))
    except Exception:
        return []
