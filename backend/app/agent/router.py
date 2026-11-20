"""Local-only API routes for streaming coding-agent sessions and approvals."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..agent_routes import require_local_agent_request
from .loop import resolve_approval, run_agent

router = APIRouter(
    prefix="/api/agent",
    tags=["coding-agent"],
    dependencies=[Depends(require_local_agent_request)],
)


class AgentRequest(BaseModel):
    task: str = Field(min_length=1, max_length=8000)
    project_root: str = Field(min_length=1, max_length=2048)
    model: str = Field(default="", max_length=120)
    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=128)


class ApprovalRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    approval_id: str = Field(min_length=1, max_length=128)
    decision: Literal["allow_once", "allow_always", "deny"]


def _validate_project_root(raw: str) -> Path:
    try:
        root = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=f"Project path is invalid: {exc}") from exc
    if not root.is_dir():
        raise HTTPException(status_code=422, detail="Project path must be an existing directory.")
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise HTTPException(status_code=422, detail="Choose a project folder, not a drive root or your home folder.")
    return root


@router.post("/stream")
async def stream_agent(request: AgentRequest) -> StreamingResponse:
    root = _validate_project_root(request.project_root)

    async def events():
        start = {"type": "session", "session_id": request.session_id}
        yield f"data: {json.dumps(start, ensure_ascii=False)}\n\n"
        async for event in run_agent(
            request.task,
            str(root),
            request.model,
            request.session_id,
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/approve")
async def approve_agent_action(request: ApprovalRequest) -> dict[str, bool]:
    if not resolve_approval(request.session_id, request.approval_id, request.decision):
        raise HTTPException(status_code=404, detail="No pending approval with that id.")
    return {"ok": True}

