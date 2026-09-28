"""Shared Ollama tool loop and per-session agent state."""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from .config import DEFAULT_MODEL, MAX_STEPS, MODES, NUM_CTX
from .llm import LLMError, chat_stream
from .permissions import Permissions
from .tools import Context, ToolError, build_tools

SYSTEM_PROMPT = """You are Friday, a local coding agent. Work only inside the supplied workspace root. Use the available tools to inspect the project before making changes. Never claim a tool action succeeded unless its result confirms it. Ask for approval when the permission system requires it. In plan mode, research using read-only tools and present a concise plan with exit_plan_mode; do not edit files or run shell commands. Keep tool calls focused and summarize the result when finished."""


@dataclass
class AgentSession:
    id: str
    root: Path
    model: str
    permissions: Permissions
    context: Context
    messages: list[dict[str, Any]]
    read_only: bool = False
    busy: bool = False
    pending_approvals: dict[str, asyncio.Future[str]] = field(default_factory=dict)

    @property
    def mode(self) -> str:
        return self.permissions.mode


def create_session(
    root: Path,
    model: str | None = None,
    mode: str = "default",
    *,
    read_only: bool = False,
) -> AgentSession:
    workspace = root.resolve()
    selected_mode = mode if mode in MODES else "default"
    context = Context(root=workspace)
    selected_model = (model or DEFAULT_MODEL).strip()
    if not selected_model or len(selected_model) > 120:
        raise ValueError("Model name must contain between 1 and 120 characters.")

    async def run_subagent(prompt: str) -> str:
        child = create_session(workspace, selected_model, "plan", read_only=True)
        output: list[str] = []
        async for event in run_turn(child, prompt):
            if event["type"] == "token":
                output.append(event["content"])
            elif event["type"] == "error":
                raise ToolError(event["message"])
        return "".join(output) or "The read-only subagent returned no text."

    context.subagent_runner = run_subagent
    return AgentSession(
        id=uuid.uuid4().hex,
        root=workspace,
        model=selected_model,
        permissions=Permissions(workspace, selected_mode),
        context=context,
        messages=[
            {
                "role": "system",
                "content": f"{SYSTEM_PROMPT}\nWorkspace root: {workspace}",
            }
        ],
        read_only=read_only,
    )


def _tool_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, dict):
            return decoded
    raise ToolError("Tool arguments must be a JSON object.")


def undo(session: AgentSession) -> str:
    if session.busy:
        raise RuntimeError("Wait for the current agent turn to finish before undoing.")
    if not session.context.checkpoints:
        raise ValueError("There are no file changes to undo.")
    path, previous = session.context.checkpoints.pop()
    if previous is None:
        path.unlink(missing_ok=True)
        session.context.read_files.discard(path)
        return f"Removed {session.context.rel(path)}"
    path.write_text(previous, encoding="utf-8")
    session.context.read_files.add(path)
    return f"Restored {session.context.rel(path)}"


async def run_turn(
    session: AgentSession, prompt: str, *, reserved: bool = False
) -> AsyncIterator[dict[str, Any]]:
    if session.busy and not reserved:
        yield {"type": "error", "message": "This agent session already has a turn running."}
        return
    prompt = prompt.strip()
    if not prompt or len(prompt) > 4000:
        yield {"type": "error", "message": "Prompt must contain between 1 and 4000 characters."}
        return

    if not reserved:
        session.busy = True
    tools = build_tools(read_only=session.read_only)
    schemas = [tool.schema() for tool in tools.values()]
    session.messages.append({"role": "user", "content": prompt})
    yield {"type": "turn_start", "model": session.model, "mode": session.mode}

    try:
        for step in range(MAX_STEPS):
            content_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            async for chunk in chat_stream(
                session.model,
                session.messages,
                tools=schemas,
                options={"num_ctx": NUM_CTX},
            ):
                message = chunk.get("message") or {}
                content = message.get("content") or ""
                if content:
                    content_parts.append(content)
                    yield {"type": "token", "content": content}
                tool_calls.extend(message.get("tool_calls") or [])
                if chunk.get("done"):
                    break

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(content_parts),
            }
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            session.messages.append(assistant_message)

            if not tool_calls:
                yield {
                    "type": "done",
                    "mode": session.mode,
                    "todos": session.context.todos,
                    "undo_count": len(session.context.checkpoints),
                }
                return

            for call in tool_calls:
                function = call.get("function") or {}
                name = function.get("name", "")
                call_id = str(call.get("id") or uuid.uuid4().hex)
                try:
                    arguments = _tool_arguments(function.get("arguments", {}))
                except (TypeError, ValueError, json.JSONDecodeError, ToolError) as exc:
                    result = f"Tool error: {exc}"
                    tool_name = name or "unknown"
                    yield {"type": "tool_result", "id": call_id, "tool": tool_name, "result": result}
                    session.messages.append({"role": "tool", "tool_name": tool_name, "content": result})
                    continue

                tool = tools.get(name)
                if tool is None:
                    result = f"Tool error: unknown tool '{name}'."
                    yield {"type": "tool_result", "id": call_id, "tool": name, "result": result}
                    session.messages.append({"role": "tool", "tool_name": name, "content": result})
                    continue

                yield {"type": "tool_start", "id": call_id, "tool": name, "arguments": arguments}
                decision, reason = session.permissions.check(tool, arguments)
                if decision == "ask":
                    approval_id = uuid.uuid4().hex
                    future = asyncio.get_running_loop().create_future()
                    session.pending_approvals[approval_id] = future
                    preview = tool.preview(arguments, session.context) if tool.preview else ""
                    yield {
                        "type": "approval_required",
                        "id": approval_id,
                        "tool": name,
                        "arguments": arguments,
                        "preview": preview,
                        "reason": reason,
                    }
                    try:
                        decision = await future
                    finally:
                        session.pending_approvals.pop(approval_id, None)
                    if decision == "always":
                        session.permissions.remember(tool, arguments)
                        decision = "allow"
                    elif decision != "allow":
                        decision = "deny"

                if decision == "deny":
                    result = f"Permission denied: {reason or 'the user declined this action.'}"
                else:
                    if name == "exit_plan_mode" and session.mode == "plan":
                        session.permissions.mode = "default"
                    try:
                        result = await tool.run(arguments, session.context)
                    except Exception as exc:
                        result = f"Tool error: {exc}"

                session.messages.append({"role": "tool", "tool_name": name, "content": result})
                yield {
                    "type": "tool_result",
                    "id": call_id,
                    "tool": name,
                    "result": result,
                    "mode": session.mode,
                    "undo_count": len(session.context.checkpoints),
                }
                if name == "todo_write":
                    yield {"type": "todos", "items": session.context.todos}

        final = f"Stopped after {MAX_STEPS} tool-loop steps."
        session.messages.append({"role": "assistant", "content": final})
        yield {"type": "token", "content": final}
        yield {
            "type": "done",
            "mode": session.mode,
            "todos": session.context.todos,
            "undo_count": len(session.context.checkpoints),
        }
    except LLMError as exc:
        yield {"type": "error", "message": str(exc)}
        yield {"type": "done", "mode": session.mode, "undo_count": len(session.context.checkpoints)}
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        yield {"type": "error", "message": f"Agent turn failed: {exc}"}
        yield {"type": "done", "mode": session.mode, "undo_count": len(session.context.checkpoints)}
    finally:
        session.busy = False
        for future in session.pending_approvals.values():
            if not future.done():
                future.set_result("deny")
        session.pending_approvals.clear()
