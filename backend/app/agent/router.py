"""Compatibility routes for the legacy approval-gated agent stream."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .loop import resolve_approval, run_agent


router = APIRouter()


class LegacyAgentRequest(BaseModel):
    task: str = Field(min_length=1, max_length=4000)
    project_root: str = Field(min_length=1, max_length=2048)
    model: str = Field(default="", max_length=120)
    session_id: str = Field(min_length=1, max_length=128)


class LegacyApprovalRequest(BaseModel):
    decision: Literal["allow_once", "allow_always", "deny"]


def _require_local(request: Request) -> None:
    from ..security import is_trusted_client, presented_token, token_valid

    if not (is_trusted_client(request.client.host if request.client else "") or token_valid(presented_token(request))):
        raise HTTPException(status_code=403, detail="Agent access is restricted to this machine.")


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


@router.post("/api/agent/stream")
async def legacy_agent_stream(body: LegacyAgentRequest, _: None = Depends(_require_local)):
    root = _validate_project_root(body.project_root)

    async def events():
        async for event in run_agent(body.task, str(root), body.model, body.session_id):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@router.post("/api/agent/stream/approve/{session_id}/{approval_id}")
async def legacy_agent_approval(
    session_id: str,
    approval_id: str,
    body: LegacyApprovalRequest,
    _: None = Depends(_require_local),
) -> dict[str, bool]:
    return {"resolved": resolve_approval(session_id, approval_id, body.decision)}
