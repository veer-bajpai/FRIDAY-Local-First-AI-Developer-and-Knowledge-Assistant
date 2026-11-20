"""LEGACY approval-gated tool loop behind /api/agent/stream (not used by the web UI or terminal).

The maintained agent is friday_agent.engine (/api/agent/sessions). This module is kept for
external clients of /api/agent/stream; it shares tools and the repository map with the engine.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from friday_agent.tools import Context, ToolError, _truncate

from .llm import DEFAULT_MODEL, chat
from .memory import build_system_prompt, maybe_compact
from .tools import AgentTool, get_tools, make_preview, tool_specs

MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "25"))
APPROVAL_TIMEOUT = 300
MAX_SESSIONS = 128
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
TRAJECTORY_PATH = Path(os.getenv("FRIDAY_DATA_DIR", "./data")) / "trajectories.jsonl"
_trajectory_lock = asyncio.Lock()


@dataclass
class SessionState:
    always_allow: set[str] = field(default_factory=set)
    pending: dict[str, asyncio.Future[str]] = field(default_factory=dict)


SESSIONS: dict[str, SessionState] = {}


def get_session(session_id: str) -> SessionState:
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError("Session ID contains unsupported characters.")
    session = SESSIONS.get(session_id)
    if session is None:
        if len(SESSIONS) >= MAX_SESSIONS:
            expired = next((key for key, value in SESSIONS.items() if not value.pending), None)
            if expired is None:
                raise RuntimeError("All coding-agent session slots are busy.")
            SESSIONS.pop(expired, None)
        session = SESSIONS[session_id] = SessionState()
    return session


def resolve_approval(session_id: str, approval_id: str, decision: str) -> bool:
    if decision not in {"allow_once", "allow_always", "deny"}:
        return False
    session = SESSIONS.get(session_id)
    future = session.pending.get(approval_id) if session else None
    if future is None or future.done():
        return False
    future.set_result(decision)
    return True


def _parse_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        decoded = json.loads(value or "{}")
        if isinstance(decoded, dict):
            return decoded
    raise ToolError("Tool arguments must be a JSON object.")


async def _write_trajectory(record: dict[str, Any]) -> None:
    try:
        TRAJECTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        async with _trajectory_lock:
            await asyncio.to_thread(_append_line, line)
    except OSError:
        pass


def _append_line(line: str) -> None:
    with TRAJECTORY_PATH.open("a", encoding="utf-8") as trajectory_file:
        trajectory_file.write(line)


async def run_agent(
    task: str,
    project_root: str,
    model: str,
    session_id: str,
) -> AsyncIterator[dict[str, Any]]:
    started = time.monotonic()
    resolved_model = model.strip() or DEFAULT_MODEL
    record: dict[str, Any] = {
        "session_id": session_id,
        "task": task,
        "model": resolved_model,
        "project_root": project_root,
        "events": [],
    }

    def emit(event: dict[str, Any]) -> dict[str, Any]:
        record["events"].append(event)
        return event

    try:
        try:
            root = Path(project_root).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            yield emit({"type": "error", "message": f"Project path is invalid: {exc}"})
            return
        if not root.is_dir():
            yield emit({"type": "error", "message": "Project path must be an existing directory."})
            return

        session = get_session(session_id)
        context = Context(root=root)
        tools = get_tools()
        schemas = tool_specs(tools)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": build_system_prompt(root)},
            {"role": "user", "content": task},
        ]

        for step in range(MAX_STEPS):
            messages = await maybe_compact(resolved_model, messages)
            yield emit({"type": "thinking", "step": step + 1})
            reply = await chat(resolved_model, messages, schemas)
            messages.append(reply)
            content = reply.get("content") or ""
            if content:
                yield emit({"type": "text", "content": str(content)})

            calls = reply.get("tool_calls") or []
            if not calls:
                yield emit({
                    "type": "done",
                    "seconds": round(time.monotonic() - started, 1),
                    "steps": step + 1,
                })
                return

            for call in calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                call_id = str(call.get("id") or uuid.uuid4().hex[:12])
                yield emit({"type": "tool_call", "id": call_id, "name": name, "args": function.get("arguments", {})})

                tool = tools.get(name)
                result: str
                try:
                    if tool is None:
                        raise ToolError(f"Unknown tool '{name}'. Available tools: {', '.join(tools)}")
                    arguments = _parse_arguments(function.get("arguments"))
                    if tool.level != "read" and name not in session.always_allow:
                        approval_id = uuid.uuid4().hex[:12]
                        future = asyncio.get_running_loop().create_future()
                        session.pending[approval_id] = future
                        yield emit({
                            "type": "approval_request",
                            "approval_id": approval_id,
                            "id": call_id,
                            "name": name,
                            "level": tool.level,
                            "args": arguments,
                            "preview": make_preview(tool, arguments, context),
                        })
                        try:
                            decision = await asyncio.wait_for(future, timeout=APPROVAL_TIMEOUT)
                        except asyncio.TimeoutError:
                            decision = "deny"
                        finally:
                            session.pending.pop(approval_id, None)
                        if decision == "allow_always":
                            session.always_allow.add(name)
                        if decision not in {"allow_once", "allow_always"}:
                            result = "The user denied this action. Do not retry it; explain the result and choose another approach."
                        else:
                            result = await tool.run(arguments, context)
                    else:
                        result = await tool.run(arguments, context)
                except (ToolError, json.JSONDecodeError, TypeError, ValueError) as exc:
                    result = f"Tool error: {exc}"
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    result = f"Tool error: {type(exc).__name__}: {exc}"

                result = _truncate(str(result))
                yield emit({"type": "tool_result", "id": call_id, "name": name, "result": result})
                messages.append({"role": "tool", "tool_name": name, "content": result})
                if name == "todo_write":
                    yield emit({"type": "todos", "todos": context.todos})

        yield emit({"type": "error", "message": f"Stopped after {MAX_STEPS} model steps."})
    except asyncio.CancelledError:
        record["cancelled"] = True
        raise
    except Exception as exc:
        yield emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
    finally:
        record["seconds"] = round(time.monotonic() - started, 1)
        await _write_trajectory(record)
        state = SESSIONS.get(session_id)
        if state:
            for future in state.pending.values():
                if not future.done():
                    future.cancel()
            state.pending.clear()

