"""Durable agent-session storage: full transcript, task state, undo checkpoints and status.

Each session is one JSON file written atomically to ``<dir>/<session-id>.json``. A restored
session continues with its previous conversation. Nothing is ever replayed: a tool call that
was in flight when the process stopped is recorded as *not executed* so the model re-checks
the workspace before retrying, and pending approvals are dropped (they must be asked again).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .engine import AgentSession, create_session, repair_dangling_tool_calls

STORE_VERSION = 2
MAX_CHECKPOINT_BYTES = 2_000_000  # undo snapshots kept per session file
MAX_TOOL_LOG = 200
FINAL_STATES = {"completed", "failed", "cancelled", "interrupted", "idle"}


def _session_title(session: AgentSession) -> str:
    for message in session.messages[1:]:
        if message.get("role") == "user":
            return " ".join(str(message.get("content", "")).split())[:80]
    return "New session"


def snapshot(session: AgentSession) -> dict[str, Any]:
    budget = MAX_CHECKPOINT_BYTES
    turns = []
    truncated_undo = False
    for turn in session.context.turns:
        files = []
        for path, previous in turn["files"]:
            size = len(previous or "")
            if size > budget:
                truncated_undo = True
                continue
            budget -= size
            files.append([str(path), previous])
        turns.append({"msg_index": turn["msg_index"], "label": turn["label"], "files": files})
    return {
        "version": STORE_VERSION,
        "id": session.id,
        "root": str(session.root),
        "model": session.model,
        "mode": session.mode,
        "status": session.status,
        "last_error": session.last_error,
        "title": _session_title(session),
        "created_at": session.created_at,
        "updated_at": time.time(),
        "usage": dict(session.usage),
        "messages": session.messages[1:],  # the system prompt is rebuilt from current project rules
        "todos": list(session.context.todos),
        "turns": turns,
        "undo_truncated": truncated_undo,
        "tool_log": session.tool_log[-MAX_TOOL_LOG:],
        "read_files": sorted(str(p) for p in session.context.read_files),
    }


def save(session: AgentSession, directory: Path) -> Path | None:
    """Atomically write the session; returns the path or None on I/O failure."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{session.id}.json"
        temp = target.with_suffix(".json.tmp")
        temp.write_text(json.dumps(snapshot(session), ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(temp, target)
        return target
    except (OSError, TypeError, ValueError):
        return None


def delete(session_id: str, directory: Path) -> None:
    try:
        (directory / f"{session_id}.json").unlink(missing_ok=True)
    except OSError:
        pass


def restore(data: dict[str, Any]) -> AgentSession:
    """Rebuild a live session from a snapshot without re-running anything."""
    root = Path(str(data["root"])).expanduser()
    if not root.is_dir():
        raise ValueError(f"Workspace no longer exists: {root}")
    session = create_session(root, data.get("model") or None, data.get("mode") or "default")
    session.id = str(data["id"])
    session.created_at = float(data.get("created_at") or time.time())
    session.messages = [session.messages[0], *[m for m in data.get("messages", []) if isinstance(m, dict)]]
    usage = data.get("usage") or {}
    session.usage = {"in": int(usage.get("in", 0)), "out": int(usage.get("out", 0))}
    session.context.todos = list(data.get("todos") or [])
    session.tool_log = list(data.get("tool_log") or [])
    for turn in data.get("turns") or []:
        files = [(Path(path), previous) for path, previous in turn.get("files", [])]
        session.context.turns.append({"msg_index": int(turn.get("msg_index", 1)),
                                      "label": str(turn.get("label", "")), "files": files})
        session.context.checkpoints.extend(files)
    for path in data.get("read_files") or []:
        session.context.read_files.add(Path(path))

    status = str(data.get("status") or "idle")
    session.last_error = data.get("last_error")
    if status not in FINAL_STATES:  # running / awaiting_approval when the process stopped
        status = "interrupted"
        session.last_error = "The backend stopped while this task was running. Nothing was replayed."
    session.status = status
    if repair_dangling_tool_calls(session.messages):
        session.status = "interrupted"
    if session.status == "interrupted":
        session.messages.append({
            "role": "user",
            "content": ("[system note] The previous task was interrupted before it finished. Tool calls "
                        "without results were NOT executed. Re-check the current state of the files before "
                        "continuing, and continue the task when the user asks."),
        })
    return session


def load_all(directory: Path) -> list[AgentSession]:
    sessions: list[AgentSession] = []
    if not directory.is_dir():
        return sessions
    for file in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("id"):
                sessions.append(restore(data))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, RuntimeError):
            continue
    return sessions
