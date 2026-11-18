"""Local-only HTTP API for shared terminal and browser agent sessions."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import json
import os
import webbrowser
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from friday_agent.config import DEFAULT_MODEL, MAX_STEPS, MODES, NUM_CTX, load_custom_commands
from friday_agent.engine import (
    AgentSession, compact_session, context_report, create_session, rewind,
    run_turn, undo,
)
from friday_agent import session_store
from friday_agent.insights import build_report
from friday_agent.mcp_client import MCPManager
from friday_agent.llm import LLMError, _same_model, list_models, resolve_model
from friday_agent.tools import register_external_tools
from friday_agent.voice import listen

log = logging.getLogger("friday.agent")
router = APIRouter()
MCP_STATUS: dict = {"status": "not_started", "servers": [], "errors": {}}
SESSIONS: dict[str, AgentSession] = {}
MCP_MANAGER: MCPManager | None = None
MAX_SESSIONS = 32
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
PERSIST_PATH = Path(os.getenv("FRIDAY_DATA_DIR", "./data")) / "agent_sessions.json"  # legacy metadata file


def _store_dir() -> Path:
    return PERSIST_PATH.parent / "agent_sessions"


def _project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "friday_agent" / "__init__.py").is_file():
            return parent.resolve()
    raise RuntimeError("Could not locate the FRIDAY workspace root.")


PROJECT_ROOT = _project_root()


def require_local_agent_request(request: Request) -> None:
    """Agent tools touch the filesystem and shell: only this machine (or a token holder) may use them."""
    from .security import is_trusted_client, presented_token, token_valid

    has_token = token_valid(presented_token(request))
    origin = request.headers.get("origin")
    if origin and not has_token:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in LOCAL_HOSTS:
            raise HTTPException(status_code=403, detail="Agent access is restricted to local browsers.")

    host = request.client.host if request.client else ""
    if not (is_trusted_client(host) or has_token):
        raise HTTPException(status_code=403, detail="Agent access is restricted to this machine.")


def _check_mode_allowed(mode: str) -> None:
    if mode not in MODES:
        raise HTTPException(status_code=422, detail=f"Mode must be one of: {', '.join(MODES)}")
    if mode == "bypass" and os.getenv("FRIDAY_ALLOW_BYPASS", "").lower() not in {"1", "true", "yes"}:
        raise HTTPException(
            status_code=403,
            detail="Bypass mode skips approvals and is disabled. Set FRIDAY_ALLOW_BYPASS=1 to enable it locally.",
        )


def _validate_project_root(raw: str) -> Path:
    try:
        candidate = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=f"Project path is invalid: {exc}") from exc
    if not candidate.is_dir():
        raise HTTPException(status_code=422, detail="Project path must be an existing directory.")
    if candidate == Path(candidate.anchor) or candidate == Path.home().resolve():
        raise HTTPException(status_code=422, detail="Choose a project folder, not a drive root or your home folder.")
    return candidate


class CreateSessionRequest(BaseModel):
    resume_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9]+$")
    model: str | None = Field(default=None, max_length=120)
    mode: str = Field(default="default", max_length=32)
    project_root: str | None = Field(default=None, max_length=2048)


class TurnRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)


class ModeRequest(BaseModel):
    mode: str | None = Field(default=None, min_length=1, max_length=32)
    model: str | None = Field(default=None, min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:/@-]+$")


class ApprovalRequest(BaseModel):
    decision: Literal["allow", "deny", "always"]


class RewindRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=100)


async def start_mcp_servers() -> None:
    global MCP_MANAGER, MCP_STATUS
    if MCP_MANAGER is not None:
        return
    _restore_sessions()
    log.info("restored %d agent session(s)", len(SESSIONS))
    manager = MCPManager(PROJECT_ROOT / ".friday" / "mcp.json")
    try:
        await manager.start()
        errors = getattr(manager, "errors", {})
        MCP_STATUS = {"status": "degraded" if errors else "ok", "servers": sorted(manager.sessions),
                      "errors": errors}
    except Exception as exc:  # optional integration: FRIDAY keeps running without it
        MCP_STATUS = {"status": "error", "servers": [], "errors": {"mcp": str(exc)}}
        log.warning("MCP integrations unavailable: %s", exc)
    register_external_tools(manager.tools)
    MCP_MANAGER = manager


async def stop_mcp_servers() -> None:
    global MCP_MANAGER
    if MCP_MANAGER is None:
        return
    for session in list(SESSIONS.values()):
        for future in session.pending_approvals.values():
            if not future.done():
                future.set_result("deny")  # never leave a tool waiting; nothing is auto-approved
        session.save()
    manager, MCP_MANAGER = MCP_MANAGER, None
    try:
        await manager.close()
    except Exception as exc:
        log.warning("MCP shutdown error: %s", exc)


def agent_health() -> dict:
    store = _store_dir()
    try:
        store.mkdir(parents=True, exist_ok=True)
        writable = os.access(store, os.W_OK)
    except OSError:
        writable = False
    return {
        "status": "ok" if writable else "error",
        "sessions": len(SESSIONS),
        "busy": sum(1 for s in SESSIONS.values() if s.busy),
        "awaiting_approval": sum(1 for s in SESSIONS.values() if s.pending_approvals),
        "store_writable": writable,
        "mcp": MCP_STATUS,
    }


def _session_or_404(session_id: str) -> AgentSession:
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Agent session not found.")
    return session


def _attach(session: AgentSession) -> AgentSession:
    """Register a session, wire durable persistence and the inter-session message runner."""
    directory = _store_dir()
    session.persist = lambda item: session_store.save(item, directory)
    owner = session

    async def send_message(target: str, text: str) -> str:
        if len(text) > 1000:
            raise ValueError("Session messages are limited to 1000 characters.")
        matches = [item for item in SESSIONS.values()
                   if item.id != owner.id and (item.id == target or item.id.startswith(target))]
        if len(matches) != 1:
            raise ValueError("Target must uniquely identify another active agent session.")
        matches[0].inbox.append({"from": owner.id[:8], "text": text})
        return f"Message delivered to {matches[0].id[:8]}."

    session.context.session_message_runner = send_message
    SESSIONS[session.id] = session
    return session


def _persist_sessions() -> None:
    """Save every live session (full transcript, todos, checkpoints, status)."""
    for session in list(SESSIONS.values()):
        session.save()


def _restore_sessions() -> None:
    restored = 0
    for session in session_store.load_all(_store_dir()):
        if len(SESSIONS) >= MAX_SESSIONS:
            break
        _attach(session)
        restored += 1
    # Migrate the old metadata-only file once: those sessions have no transcript to resume.
    try:
        if PERSIST_PATH.exists():
            payload = json.loads(PERSIST_PATH.read_text(encoding="utf-8"))
            for item in payload if isinstance(payload, list) else []:
                sid = str(item.get("id") or "")
                root = Path(str(item.get("root", ""))).expanduser()
                if not sid or sid in SESSIONS or not root.is_dir() or len(SESSIONS) >= MAX_SESSIONS:
                    continue
                session = create_session(root, item.get("model") or DEFAULT_MODEL, item.get("mode") or "default")
                session.id = sid
                if isinstance(item.get("usage"), dict):
                    session.usage = item["usage"]
                _attach(session).save()
            PERSIST_PATH.rename(PERSIST_PATH.with_suffix(".json.migrated"))
    except (OSError, json.JSONDecodeError, ValueError, RuntimeError):
        pass


def _session_payload(session: AgentSession) -> dict:
    return {
        "id": session.id,
        "model": session.model,
        "mode": session.mode,
        "busy": session.busy,
        "workspace": session.root.name,
        "todos": session.context.todos,
        "undo_count": len(session.context.checkpoints),
        "max_steps": MAX_STEPS,
        "num_ctx": NUM_CTX,
        "usage": session.usage,
        "status": session.status,
        "last_error": session.last_error,
        "messages": len(session.messages) - 1,
        "resumable": len(session.messages) > 1,
    }


@router.get("/api/agent/models", dependencies=[Depends(require_local_agent_request)])
async def agent_models() -> dict:
    return {
        "models": await list_models(),
        "default_model": DEFAULT_MODEL,
        "modes": MODES,
        "max_steps": MAX_STEPS,
        "num_ctx": NUM_CTX,
        "workspace": PROJECT_ROOT.name,
    }


@router.get("/api/agent/commands", dependencies=[Depends(require_local_agent_request)])
async def agent_commands() -> dict[str, str]:
    return load_custom_commands(PROJECT_ROOT)


@router.post("/api/agent/voice", dependencies=[Depends(require_local_agent_request)])
async def agent_voice() -> dict[str, str]:
    try:
        text = await asyncio.to_thread(listen)
    except (RuntimeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"text": text}


@router.get("/api/agent/insights", dependencies=[Depends(require_local_agent_request)])
async def agent_insights() -> dict[str, str]:
    report = build_report(PROJECT_ROOT)
    await asyncio.to_thread(webbrowser.open, report.as_uri())
    return {"path": str(report)}


@router.post("/api/agent/sessions", dependencies=[Depends(require_local_agent_request)])
async def create_agent_session(body: CreateSessionRequest) -> dict:
    _check_mode_allowed(body.mode)
    if body.resume_id:
        existing = SESSIONS.get(body.resume_id)
        if existing is None:
            # Not in memory (evicted, or the app started without the startup hook): try disk.
            loaded = session_store.load_one(body.resume_id, _store_dir())
            if loaded is not None and len(SESSIONS) < MAX_SESSIONS:
                existing = _attach(loaded)
        if existing is None:
            raise HTTPException(status_code=404, detail="That agent session no longer exists.")
        return _session_payload(existing)
    available = [session_id for session_id, session in SESSIONS.items() if not session.busy]
    while len(SESSIONS) >= MAX_SESSIONS and available:
        evicted = SESSIONS.pop(available.pop(0), None)
        if evicted is not None:
            evicted.save()  # evicted from memory only; stays resumable on disk after restart
    if len(SESSIONS) >= MAX_SESSIONS:
        raise HTTPException(status_code=503, detail="All agent session slots are busy.")
    root = PROJECT_ROOT
    if body.project_root:
        root = _validate_project_root(body.project_root)
    model, note = body.model, None
    if not model:
        try:
            model, note = await resolve_model(DEFAULT_MODEL, purpose="agent")
        except LLMError as exc:
            model, note = DEFAULT_MODEL, str(exc)  # Ollama offline: create anyway, the turn reports it
    try:
        session = create_session(root, model, body.mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _attach(session).save()
    return {**_session_payload(session), "model_note": note}


@router.get("/api/agent/sessions", dependencies=[Depends(require_local_agent_request)])
async def list_agent_sessions() -> list[dict]:
    return [
        {"id": session.id[:8], "full_id": session.id, "workspace": session.root.name, "busy": session.busy,
         "mode": session.mode, "status": session.status, "title": session_store._session_title(session),
         "messages": len(session.messages) - 1}
        for session in SESSIONS.values()
    ]


@router.get("/api/agent/sessions/{session_id}/history", dependencies=[Depends(require_local_agent_request)])
async def agent_session_history(session_id: str, limit: int = 200) -> dict:
    """Conversation and tool activity for a session, so clients can show and resume it."""
    session = _session_or_404(session_id)
    shown = []
    for message in session.messages[1:][-max(1, min(limit, 1000)):]:
        item = {"role": message.get("role"), "content": str(message.get("content", ""))[:8000]}
        if message.get("tool_name"):
            item["tool"] = message["tool_name"]
        if message.get("tool_calls"):
            item["tool_calls"] = [(call.get("function") or {}).get("name") for call in message["tool_calls"]]
        shown.append(item)
    return {**_session_payload(session), "history": shown, "tool_log": session.tool_log[-50:]}


@router.get("/api/agent/sessions/{session_id}", dependencies=[Depends(require_local_agent_request)])
async def get_agent_session(session_id: str) -> dict:
    return _session_payload(_session_or_404(session_id))


@router.patch("/api/agent/sessions/{session_id}", dependencies=[Depends(require_local_agent_request)])
async def update_agent_session(session_id: str, body: ModeRequest) -> dict:
    session = _session_or_404(session_id)
    if session.busy:
        raise HTTPException(status_code=409, detail="Wait for the current turn to finish before changing modes.")
    if body.mode is None and body.model is None:
        raise HTTPException(status_code=422, detail="Send a mode or a model to change.")
    if body.model is not None:
        try:
            installed = await list_models()
        except Exception:  # Ollama offline: cannot confirm the model exists
            installed = []
        match = next((name for name in installed if _same_model(name, body.model)), None)
        if match is None:
            available = ", ".join(installed) or "none (is Ollama running?)"
            raise HTTPException(status_code=422, detail=f"Model '{body.model}' is not installed. Installed: {available}")
        session.model = match
    if body.mode is not None:
        _check_mode_allowed(body.mode)
        session.permissions.mode = body.mode
    session.save()
    return _session_payload(session)


@router.post("/api/agent/sessions/{session_id}/turns", dependencies=[Depends(require_local_agent_request)])
async def agent_turn(session_id: str, body: TurnRequest) -> StreamingResponse:
    session = _session_or_404(session_id)
    if session.busy:
        raise HTTPException(status_code=409, detail="This agent session already has a turn running.")
    session.busy = True
    if session.inbox:
        messages = session.inbox[:]
        session.inbox.clear()
        prefix = "\n".join(f"[message from agent {item['from']}]: {item['text']}" for item in messages)
        body.prompt = f"{prefix}\n\n{body.prompt}"

    async def events():
        try:
            async for event in run_turn(session, body.prompt, reserved=True):
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        finally:
            session.busy = False
            session.save()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/api/agent/sessions/{session_id}/approvals/{approval_id}",
    dependencies=[Depends(require_local_agent_request)],
)
async def approve_tool_call(session_id: str, approval_id: str, body: ApprovalRequest) -> dict:
    session = _session_or_404(session_id)
    future = session.pending_approvals.get(approval_id)
    if future is None or future.done():
        raise HTTPException(status_code=404, detail="Approval request is no longer pending.")
    future.set_result(body.decision)
    session.save()
    return {"approved": body.decision != "deny", "decision": body.decision}


@router.post("/api/agent/sessions/{session_id}/undo", dependencies=[Depends(require_local_agent_request)])
async def undo_agent_change(session_id: str) -> dict:
    session = _session_or_404(session_id)
    try:
        message = undo(session)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.save()
    return {"message": message, "undo_count": len(session.context.checkpoints)}


@router.post("/api/agent/sessions/{session_id}/rewind", dependencies=[Depends(require_local_agent_request)])
async def rewind_agent_turn(session_id: str, body: RewindRequest) -> dict:
    session = _session_or_404(session_id)
    try:
        labels = rewind(session, body.count)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.save()
    return {"rewound": labels, "undo_count": len(session.context.checkpoints)}


@router.get("/api/agent/sessions/{session_id}/context", dependencies=[Depends(require_local_agent_request)])
async def agent_context(session_id: str) -> dict:
    return context_report(_session_or_404(session_id))


@router.post("/api/agent/sessions/{session_id}/compact", dependencies=[Depends(require_local_agent_request)])
async def compact_agent_session(session_id: str) -> dict:
    session = _session_or_404(session_id)
    try:
        summary = await compact_session(session)
        session.save()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not compact session: {exc}") from exc
    return {"summary": summary, **context_report(session)}


@router.delete("/api/agent/sessions/{session_id}", dependencies=[Depends(require_local_agent_request)])
async def delete_agent_session(session_id: str) -> dict:
    session = _session_or_404(session_id)
    if session.busy:
        raise HTTPException(status_code=409, detail="Finish or stop the active turn before closing this session.")
    SESSIONS.pop(session_id, None)
    session_store.delete(session_id, _store_dir())
    return {"deleted": True}
