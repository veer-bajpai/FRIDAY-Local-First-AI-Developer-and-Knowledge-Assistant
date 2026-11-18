"""Legacy Ollama chat adapter for the external /api/agent/stream loop."""
from __future__ import annotations

from typing import Any

from friday_agent.config import DEFAULT_MODEL
from friday_agent.llm import chat_stream


async def chat(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    content: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    usage: dict[str, int] = {}
    async for chunk in chat_stream(model or DEFAULT_MODEL, messages, tools=tools):
        message = chunk.get("message") or {}
        if message.get("content"):
            content.append(str(message["content"]))
        if message.get("tool_calls"):
            tool_calls.extend(message["tool_calls"])
        if chunk.get("done"):
            usage = {
                "prompt_eval_count": int(chunk.get("prompt_eval_count", 0) or 0),
                "eval_count": int(chunk.get("eval_count", 0) or 0),
            }
    return {
        "role": "assistant",
        "content": "".join(content),
        "tool_calls": tool_calls,
        "_usage": usage,
    }
