"""Shared Ollama tool loop and per-session agent state."""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from .config import DEFAULT_MODEL, MAX_STEPS, MODES, NUM_CTX, NUM_PREDICT, load_project_memory, load_settings
from .hooks import Hooks
from .llm import LLMError, chat_stream
from .permissions import Permissions
from .repo_map import repo_map
from .tools import Context, ToolError, build_tools, read_text_keep, write_text_keep


def _restore_file(path: Path, previous: str) -> None:
    """Restore checkpoint text, keeping the file's current newline style."""
    newline = "\n"
    if path.is_file():
        try:
            newline = read_text_keep(path)[1]
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_keep(path, previous, newline)

REPO_MAP_FILES = int(os.getenv("FRIDAY_REPO_MAP_FILES", "120"))

SYSTEM_PROMPT = """You are Friday, a local coding agent. Work only inside the supplied workspace root. Use the available tools to inspect the project before making changes. Never claim a tool action succeeded unless its result confirms it. Ask for approval when the permission system requires it. In plan mode, research using read-only tools and present a concise plan with exit_plan_mode; do not edit files or run shell commands. Keep tool calls focused and summarize the result when finished.

Work method: for multi-step tasks keep a todo list with todo_write. Search (grep/glob) before reading whole files; read a file before editing it; prefer small edit_file changes. After changing code, run the relevant tests or checks and fix failures before reporting. If a tool fails, read the error and change approach instead of repeating the same call. Finish with the files changed and the checks you ran."""


@dataclass
class AgentSession:
    id: str
    root: Path
    model: str
    permissions: Permissions
    context: Context
    messages: list[dict[str, Any]]
    read_only: bool = False
    hooks: Hooks = field(default_factory=Hooks)
    busy: bool = False
    pending_approvals: dict[str, asyncio.Future[str]] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=lambda: {"in": 0, "out": 0})
    inbox: list[dict[str, str]] = field(default_factory=list)
    # idle | running | awaiting_approval | completed | failed | cancelled | interrupted
    status: str = "idle"
    last_error: str | None = None
    created_at: float = field(default_factory=time.time)
    tool_log: list[dict[str, Any]] = field(default_factory=list)
    persist: Callable[["AgentSession"], Any] | None = None

    @property
    def mode(self) -> str:
        return self.permissions.mode

    def save(self) -> None:
        if self.persist is not None:
            try:
                self.persist(self)
            except Exception:  # persistence must never break a turn
                pass


def repair_dangling_tool_calls(messages: list[dict[str, Any]]) -> bool:
    """Give every assistant tool call a result so the transcript stays valid after an interruption.

    Returns True when a call had no result (it was never executed and is not replayed).
    """
    repaired = False
    index = 0
    while index < len(messages):
        message = messages[index]
        calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        if calls:
            following = index + 1
            answered = 0
            while following < len(messages) and messages[following].get("role") == "tool":
                answered += 1
                following += 1
            missing = calls[answered:]
            for call in missing:
                name = (call.get("function") or {}).get("name", "unknown")
                messages.insert(following, {
                    "role": "tool", "tool_name": name,
                    "content": "Not executed: the task was interrupted before this tool ran.",
                })
                following += 1
                repaired = True
            index = following
            continue
        index += 1
    return repaired


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
    settings = load_settings(workspace)
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

    async def run_subagents(prompts: list[str]) -> str:
        results = await asyncio.gather(*(run_subagent(prompt) for prompt in prompts))
        return "\n\n---\n\n".join(
            f"Subtask {index}:\n{result}" for index, result in enumerate(results, 1)
        )

    context.subagent_runner = run_subagent
    context.subagent_parallel_runner = run_subagents
    return AgentSession(
        id=uuid.uuid4().hex,
        root=workspace,
        model=selected_model,
        permissions=Permissions(workspace, selected_mode, settings),
        context=context,
        messages=[
            {
                "role": "system",
                "content": (f"{SYSTEM_PROMPT}\nWorkspace root: {workspace}\n"
                            f"\n# Project rules (FRIDAY.md)\n{load_project_memory(workspace)}"
                            f"\n\n# Repository overview\n{repo_map(workspace, REPO_MAP_FILES)}"),
            }
        ],
        read_only=read_only,
        hooks=Hooks(settings.get("hooks", {}), workspace),
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
    checkpoint = (path, previous)
    for turn in reversed(session.context.turns):
        if checkpoint in turn["files"]:
            turn["files"].remove(checkpoint)
            break
    if previous is None:
        path.unlink(missing_ok=True)
        session.context.read_files.discard(path)
        return f"Removed {session.context.rel(path)}"
    _restore_file(path, previous)
    session.context.read_files.add(path)
    return f"Restored {session.context.rel(path)}"


def rewind(session: AgentSession, count: int = 1) -> list[str]:
    if session.busy:
        raise RuntimeError("Wait for the current agent turn to finish before rewinding.")
    if count < 1:
        raise ValueError("Rewind count must be at least 1.")
    undone = []
    for _ in range(min(count, len(session.context.turns))):
        turn = session.context.turns.pop()
        for path, previous in reversed(turn["files"]):
            if previous is None:
                path.unlink(missing_ok=True)
                session.context.read_files.discard(path)
            else:
                _restore_file(path, previous)
                session.context.read_files.add(path)
            try:
                session.context.checkpoints.remove((path, previous))
            except ValueError:
                pass
        undone.append(turn)
    if undone:
        session.messages = session.messages[:undone[-1]["msg_index"]]
    return [turn["label"] for turn in undone]


def context_report(session: AgentSession) -> dict[str, int]:
    estimate = len(json.dumps(session.messages, default=str)) // 4
    return {
        "messages": len(session.messages),
        "estimated_tokens": estimate,
        "input_tokens": session.usage["in"],
        "output_tokens": session.usage["out"],
        "undo_count": len(session.context.checkpoints),
    }


def _log_tool_call(session: AgentSession, name: str, result: str) -> None:
    path = session.root / ".friday" / "usage.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as log:
            log.write(json.dumps({
                "ts": time.time(),
                "tool": name,
                "ok": not result.startswith(("Tool error:", "Permission denied:", "Blocked by", "User declined")),
                **session.usage,
            }) + "\n")
    except OSError:
        pass


async def compact_session(session: AgentSession) -> str:
    if session.busy:
        raise RuntimeError("Wait for the current agent turn to finish before compacting.")
    session.busy = True
    transcript = json.dumps(session.messages[1:], ensure_ascii=False, default=str)[:150_000]
    compact_messages = [
        session.messages[0],
        {"role": "user", "content": "Summarize this session for continuation: goals, decisions, "
         "files changed, and open tasks. Preserve actionable details.\n\n" + transcript},
    ]
    summary_parts = []
    try:
        async for chunk in chat_stream(
            session.model, compact_messages,
            options={"num_ctx": NUM_CTX, "num_predict": NUM_PREDICT},
        ):
            message = chunk.get("message") or {}
            if message.get("content"):
                summary_parts.append(message["content"])
            if chunk.get("done"):
                session.usage["in"] += int(chunk.get("prompt_eval_count", 0) or 0)
                session.usage["out"] += int(chunk.get("eval_count", 0) or 0)
        summary = "".join(summary_parts).strip()
        if not summary:
            raise LLMError("The model returned an empty session summary.")
        session.messages = [session.messages[0], {"role": "user", "content": "Session summary:\n" + summary}]
        for turn in session.context.turns:
            turn["msg_index"] = 1
        return summary
    finally:
        session.busy = False


TOOL_TIMEOUT = float(os.getenv("FRIDAY_TOOL_TIMEOUT", "300"))
MAX_IDENTICAL_FAILURES = 3
CONTEXT_TRIM_RATIO = 0.8
KEEP_RECENT_TOOL_OUTPUTS = 6
FAIL_PREFIXES = ("Tool error:", "Permission denied:", "Blocked by", "User declined", "Not executed")


def _signature(name: str, arguments: dict[str, Any]) -> str:
    return name + ":" + json.dumps(arguments, sort_keys=True, default=str)[:2000]


def _target(arguments: dict[str, Any]) -> str:
    for key in ("command", "path", "pattern", "query", "url", "prompt"):
        if arguments.get(key):
            return str(arguments[key])[:200]
    return ""


def trim_context(session: AgentSession) -> int:
    """Shrink old tool outputs when the transcript nears the model context window.

    The most recent tool results stay intact; older ones keep a short head so the model
    still knows what happened. Returns the number of messages trimmed.
    """
    budget = int(NUM_CTX * CONTEXT_TRIM_RATIO)
    if len(json.dumps(session.messages, default=str)) // 4 <= budget:
        return 0
    tool_indexes = [i for i, m in enumerate(session.messages) if m.get("role") == "tool"]
    trimmed = 0
    for index in tool_indexes[:-KEEP_RECENT_TOOL_OUTPUTS]:
        content = str(session.messages[index].get("content", ""))
        if len(content) > 600 and not content.endswith("[older output trimmed to save context]"):
            session.messages[index]["content"] = content[:400] + "\n... [older output trimmed to save context]"
            trimmed += 1
    return trimmed


def _finish(session: AgentSession, status: str, error: str | None = None) -> None:
    session.status = status
    session.last_error = error
    session.save()


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
    repair_dangling_tool_calls(session.messages)
    session.context.begin_turn(len(session.messages), prompt)
    tools = build_tools(read_only=session.read_only)
    schemas = [tool.schema() for tool in tools.values()]
    session.messages.append({"role": "user", "content": prompt})
    session.status, session.last_error = "running", None
    session.save()
    yield {"type": "turn_start", "model": session.model, "mode": session.mode}

    failures: dict[str, int] = {}
    denied: set[str] = set()
    try:
        for step in range(MAX_STEPS):
            if trim_context(session):
                yield {"type": "context_trimmed", "estimated_tokens": len(json.dumps(session.messages, default=str)) // 4}
            content_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            async for chunk in chat_stream(
                session.model,
                session.messages,
                tools=schemas,
                options={"num_ctx": NUM_CTX, "num_predict": NUM_PREDICT},
            ):
                message = chunk.get("message") or {}
                content = message.get("content") or ""
                if content:
                    content_parts.append(content)
                    yield {"type": "token", "content": content}
                tool_calls.extend(message.get("tool_calls") or [])
                if chunk.get("done"):
                    session.usage["in"] += int(chunk.get("prompt_eval_count", 0) or 0)
                    session.usage["out"] += int(chunk.get("eval_count", 0) or 0)
                    break

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(content_parts),
            }
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            session.messages.append(assistant_message)

            if not tool_calls:
                _finish(session, "completed")
                yield {
                    "type": "done",
                    "mode": session.mode,
                    "status": session.status,
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
                    result = f"Tool error: unknown tool '{name}'. Available: {', '.join(sorted(tools))}."
                    yield {"type": "tool_result", "id": call_id, "tool": name, "result": result}
                    session.messages.append({"role": "tool", "tool_name": name, "content": result})
                    continue

                signature = _signature(name, arguments)
                yield {"type": "tool_start", "id": call_id, "tool": name, "arguments": arguments}
                approval_note = None
                if failures.get(signature, 0) >= MAX_IDENTICAL_FAILURES:
                    decision, reason = "deny", ""
                    result = (f"Tool error: this exact {name} call already failed {failures[signature]} times. "
                              "Do not repeat it; inspect the error and change approach.")
                elif signature in denied:
                    decision, reason = "deny", ""
                    result = "Permission denied: the user already declined this exact action in this turn."
                else:
                    decision, reason = session.permissions.check(tool, arguments)
                    result = ""
                if decision == "ask":
                    approval_id = uuid.uuid4().hex
                    future = asyncio.get_running_loop().create_future()
                    session.pending_approvals[approval_id] = future
                    preview = tool.preview(arguments, session.context) if tool.preview else ""
                    session.status = "awaiting_approval"
                    session.save()
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
                        session.status = "running"
                    approval_note = decision
                    if decision == "always":
                        session.permissions.remember(tool, arguments)
                        decision = "allow"
                    elif decision != "allow":
                        decision = "deny"
                        denied.add(signature)

                if decision == "deny":
                    result = result or f"Permission denied: {reason or 'the user declined this action.'}"
                else:
                    hook_ok, hook_reason = session.hooks.run("PreToolUse", name, arguments)
                    if not hook_ok:
                        result = f"Blocked by PreToolUse hook: {hook_reason}"
                        session.messages.append({"role": "tool", "tool_name": name, "content": result})
                        yield {
                            "type": "tool_result", "id": call_id, "tool": name,
                            "result": result, "mode": session.mode,
                            "undo_count": len(session.context.checkpoints),
                        }
                        continue
                    if name == "exit_plan_mode" and session.mode == "plan":
                        session.permissions.mode = "default"
                    limit = TOOL_TIMEOUT
                    if name == "bash":
                        limit = max(limit, min(float(arguments.get("timeout", 120) or 120), 600) + 15)
                    try:
                        result = await asyncio.wait_for(tool.run(arguments, session.context), limit)
                    except asyncio.TimeoutError:
                        result = f"Tool error: {name} did not finish within {int(limit)}s and was stopped."
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        result = f"Tool error: {exc}"
                    session.hooks.run("PostToolUse", name, arguments, result)

                ok = not str(result).startswith(FAIL_PREFIXES)
                if not ok and decision != "deny":
                    failures[signature] = failures.get(signature, 0) + 1
                _log_tool_call(session, name, result)
                session.tool_log.append({"ts": time.time(), "tool": name, "target": _target(arguments),
                                         "ok": ok, "approval": approval_note})
                session.messages.append({"role": "tool", "tool_name": name, "content": result})
                session.save()
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

        final = f"Stopped after {MAX_STEPS} tool-loop steps. Ask me to continue to keep going."
        session.messages.append({"role": "assistant", "content": final})
        _finish(session, "completed")
        yield {"type": "token", "content": final}
        yield {
            "type": "done",
            "mode": session.mode,
            "status": session.status,
            "todos": session.context.todos,
            "undo_count": len(session.context.checkpoints),
        }
    except LLMError as exc:
        repair_dangling_tool_calls(session.messages)
        _finish(session, "failed", str(exc))
        yield {"type": "error", "message": str(exc)}
        yield {"type": "done", "mode": session.mode, "status": session.status,
               "undo_count": len(session.context.checkpoints)}
    except (asyncio.CancelledError, GeneratorExit):
        repair_dangling_tool_calls(session.messages)
        _finish(session, "cancelled", "The turn was cancelled.")
        raise
    except Exception as exc:
        repair_dangling_tool_calls(session.messages)
        _finish(session, "failed", f"Agent turn failed: {exc}")
        yield {"type": "error", "message": f"Agent turn failed: {exc}"}
        yield {"type": "done", "mode": session.mode, "status": session.status,
               "undo_count": len(session.context.checkpoints)}
    finally:
        session.busy = False
        for future in session.pending_approvals.values():
            if not future.done():
                future.set_result("deny")
        session.pending_approvals.clear()

