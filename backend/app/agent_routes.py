"""Local-only HTTP API for shared terminal and browser agent sessions."""
from __future__ import annotations

import asyncio
import ipaddress
import json
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
from friday_agent.insights import build_report
from friday_agent.mcp_client import MCPManager
from friday_agent.llm import list_models
from friday_agent.tools import register_external_tools
from friday_agent.voice import listen

router = APIRouter()
SESSIONS: dict[str, AgentSession] = {}
MCP_MANAGER: MCPManager | None = None
MAX_SESSIONS = 32
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "friday_agent" / "__init__.py").is_file():
            return parent.resolve()
    raise RuntimeError("Could not locate the FRIDAY workspace root.")


PROJECT_ROOT = _project_root()


def require_local_agent_request(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in LOCAL_HOSTS:
            raise HTTPException(status_code=403, detail="Agent access is restricted to local browsers.")

    host = request.client.host if request.client else ""
    try:
        is_local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_local = False
    if not is_local:
        raise HTTPException(status_code=403, detail="Agent access is restricted to this machine.")


class CreateSessionRequest(BaseModel):
    model: str | None = Field(default=None, max_length=120)
    mode: str = Field(default="default", max_length=32)


class TurnRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)


class ModeRequest(BaseModel):
    mode: str = Field(min_length=1, max_length=32)


class ApprovalRequest(BaseModel):
    decision: Literal["allow", "deny", "always"]


class RewindRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=100)


@router.on_event("startup")
async def start_mcp_servers() -> None:
    global MCP_MANAGER
    manager = MCPManager(PROJECT_ROOT / ".friday" / "mcp.json")
    await manager.start()
    register_external_tools(manager.tools)
    MCP_MANAGER = manager


@router.on_event("shutdown")
async def stop_mcp_servers() -> None:
    if MCP_MANAGER is not None:
        await MCP_MANAGER.close()


def _session_or_404(session_id: str) -> AgentSession:
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Agent session not found.")
    return session


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
    if body.mode not in MODES:
        raise HTTPException(status_code=422, detail=f"Mode must be one of: {', '.join(MODES)}")
    available = [session_id for session_id, session in SESSIONS.items() if not session.busy]
    while len(SESSIONS) >= MAX_SESSIONS and available:
        SESSIONS.pop(available.pop(0), None)
    if len(SESSIONS) >= MAX_SESSIONS:
        raise HTTPException(status_code=503, detail="All agent session slots are busy.")
    try:
        session = create_session(PROJECT_ROOT, body.model, body.mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    SESSIONS[session.id] = session
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
    return _session_payload(session)


@router.get("/api/agent/sessions", dependencies=[Depends(require_local_agent_request)])
async def list_agent_sessions() -> list[dict]:
    return [
        {"id": session.id[:8], "workspace": session.root.name, "busy": session.busy, "mode": session.mode}
        for session in SESSIONS.values()
    ]


@router.get("/api/agent/sessions/{session_id}", dependencies=[Depends(require_local_agent_request)])
async def get_agent_session(session_id: str) -> dict:
    return _session_payload(_session_or_404(session_id))


@router.patch("/api/agent/sessions/{session_id}", dependencies=[Depends(require_local_agent_request)])
async def update_agent_session(session_id: str, body: ModeRequest) -> dict:
    session = _session_or_404(session_id)
    if session.busy:
        raise HTTPException(status_code=409, detail="Wait for the current turn to finish before changing modes.")
    if body.mode not in MODES:
        raise HTTPException(status_code=422, detail=f"Mode must be one of: {', '.join(MODES)}")
    session.permissions.mode = body.mode
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
    return {"rewound": labels, "undo_count": len(session.context.checkpoints)}


@router.get("/api/agent/sessions/{session_id}/context", dependencies=[Depends(require_local_agent_request)])
async def agent_context(session_id: str) -> dict:
    return context_report(_session_or_404(session_id))


@router.post("/api/agent/sessions/{session_id}/compact", dependencies=[Depends(require_local_agent_request)])
async def compact_agent_session(session_id: str) -> dict:
    session = _session_or_404(session_id)
    try:
        summary = await compact_session(session)
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
    return {"deleted": True}
