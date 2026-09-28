"""Local-only HTTP API for shared terminal and browser agent sessions."""
from __future__ import annotations

import asyncio
import ipaddress
import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from friday_agent.config import DEFAULT_MODEL, MAX_STEPS, MODES, NUM_CTX
from friday_agent.engine import AgentSession, create_session, run_turn, undo
from friday_agent.llm import list_models

router = APIRouter()
SESSIONS: dict[str, AgentSession] = {}
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
        return

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
    return _session_payload(session)


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


@router.delete("/api/agent/sessions/{session_id}", dependencies=[Depends(require_local_agent_request)])
async def delete_agent_session(session_id: str) -> dict:
    session = _session_or_404(session_id)
    if session.busy:
        raise HTTPException(status_code=409, detail="Finish or stop the active turn before closing this session.")
    SESSIONS.pop(session_id, None)
    return {"deleted": True}
