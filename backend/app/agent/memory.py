"""Project instruction loading, compact repository maps, and history compaction."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from friday_agent.repo_map import repo_map as shared_repo_map

from .llm import chat

IGNORE_DIRS = {
    ".git", ".next", ".venv", "venv", "node_modules", "__pycache__",
    "dist", "build", "data", ".pytest_cache", ".mypy_cache",
}
MAX_INSTRUCTION_CHARS = 6000

SYSTEM_PROMPT = """You are FRIDAY Code, a careful coding agent working inside the supplied project root.

Explore before changing files. Use read tools automatically. For any write or command, show the user the approval preview and wait for their decision. If denied, explain the result and choose another approach instead of retrying the same action. For multi-step tasks, create and update todos. Verify changes with appropriate tests or checks. Never claim an action succeeded unless the tool result confirms it. Keep the final report concise and name the files changed and checks run.
"""


def load_instructions(root: Path) -> str:
    parts = []
    for name in ("CLAUDE.md", "AGENTS.md"):
        instruction_file = root / name
        if instruction_file.is_file():
            text = instruction_file.read_text(encoding="utf-8", errors="replace")
            parts.append(f"## {name}\n{text[:MAX_INSTRUCTION_CHARS]}")
    return "\n\n".join(parts) or "No root-level CLAUDE.md or AGENTS.md was found."


def repo_map(root: Path, max_files: int = 100) -> str:
    """Delegates to the shared implementation used by friday_agent.engine."""
    return shared_repo_map(root, max_files)


def build_system_prompt(root: Path) -> str:
    return (
        f"{SYSTEM_PROMPT}\nProject root: {root}\n\n"
        f"# Project instructions\n{load_instructions(root)}\n\n"
        f"# Compact repository map\n{repo_map(root)}"
    )


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    return len(json.dumps(messages, ensure_ascii=False, default=str)) // 4


async def maybe_compact(
    model: str,
    messages: list[dict[str, Any]],
    budget: int = 12000,
    keep_turns: int = 4,
) -> list[dict[str, Any]]:
    """Summarize complete oldest turns while preserving assistant/tool pairs."""
    if estimate_tokens(messages) <= budget:
        return messages

    user_turn_starts = [
        index for index, message in enumerate(messages)
        if index > 0 and message.get("role") == "user"
    ]
    if len(user_turn_starts) <= keep_turns:
        return messages

    cutoff = user_turn_starts[-keep_turns]
    old_messages = messages[1:cutoff]
    transcript = "\n".join(
        f"{message.get('role')}: {str(message.get('content', ''))[:1000]}"
        for message in old_messages
    )
    summary = await chat(model, [
        {
            "role": "system",
            "content": "Summarize the earlier coding-agent work: goal, decisions, files changed, checks run, and unresolved tasks. Preserve actionable details.",
        },
        {"role": "user", "content": transcript[:60_000]},
    ])
    note = {
        "role": "user",
        "content": f"[Summary of earlier work]\n{summary.get('content', '')}",
    }
    return [messages[0], note, *messages[cutoff:]]
