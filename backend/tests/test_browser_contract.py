"""Friday Browser contract: the API fields the browser dashboard and sidebar read.

The terminal console (Friday Chat) shares the agent session API with the browser.
These tests fail if a terminal-side change alters a field the browser depends on,
so the Chat redesign cannot silently break the Dashboard.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Fields read by src/app/ui/Dashboard.tsx (Agent runs card, Agent sessions stat).
DASHBOARD_SESSION_FIELDS = {"id", "full_id", "workspace", "busy", "mode", "status", "title", "messages"}


def test_agent_session_list_keeps_dashboard_fields(tmp_path: Path, monkeypatch):
    from backend.app import agent_routes

    monkeypatch.setattr(agent_routes, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(agent_routes, "PERSIST_PATH", tmp_path / "data" / "agent_sessions.json")
    monkeypatch.setattr(agent_routes, "SESSIONS", {})

    app = FastAPI()
    app.include_router(agent_routes.router)
    with TestClient(app) as client:
        assert client.post("/api/agent/sessions", json={"model": "llama3.2:latest"}).status_code == 200
        listed = client.get("/api/agent/sessions").json()

    assert len(listed) == 1
    session = listed[0]
    assert DASHBOARD_SESSION_FIELDS <= session.keys()
    assert isinstance(session["busy"], bool)
    assert isinstance(session["messages"], int)
    assert session["workspace"] == tmp_path.name


def test_health_keeps_dashboard_and_sidebar_fields(api):
    client, _ = api
    body = client.get("/api/health").json()
    checks = body["checks"]
    # Sidebar "Local engine" card and Dashboard "Knowledge base" health rows.
    assert body["status"] in {"ok", "degraded", "error"}
    assert "status" in checks["ollama"]
    assert "ocr" in checks["extraction"] and "vision_model" in checks["extraction"]
    assert "documents" in checks["database"]
