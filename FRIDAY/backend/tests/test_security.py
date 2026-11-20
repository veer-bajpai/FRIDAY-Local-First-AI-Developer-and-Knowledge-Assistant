"""Security layer: auth, trusted hosts, rate limits, bypass gating, workspace escape."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import security
from backend.app.security import SecurityMiddleware
from friday_agent.permissions import Permissions, args_stay_in_workspace
from friday_agent.tools import build_tools


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityMiddleware, max_json_bytes=2000)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/data")
    def data():
        return {"secret": True}

    @app.post("/api/echo")
    def echo(body: dict):
        return body

    return app


@pytest.fixture(autouse=True)
def _token(monkeypatch, tmp_path):
    monkeypatch.setenv("FRIDAY_API_TOKEN", "s3cret-token")
    monkeypatch.setenv("FRIDAY_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("FRIDAY_AUTH", raising=False)


def test_loopback_client_allowed_without_token_in_auto_mode():
    with TestClient(_app()) as client:
        assert client.get("/api/data").status_code == 200


def test_remote_client_needs_token(monkeypatch):
    monkeypatch.setattr(security, "is_trusted_client", lambda host: False)
    with TestClient(_app()) as client:
        assert client.get("/api/data").status_code == 401
        assert client.get("/api/data", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get("/api/data", headers={"Authorization": "Bearer s3cret-token"}).status_code == 200
        assert client.get("/api/data", headers={"X-Friday-Token": "s3cret-token"}).status_code == 200
        assert client.get("/health").status_code == 200


def test_required_mode_applies_to_loopback(monkeypatch):
    monkeypatch.setenv("FRIDAY_AUTH", "required")
    with TestClient(_app()) as client:
        assert client.get("/api/data").status_code == 401
        assert client.get("/api/data", headers={"X-Friday-Token": "s3cret-token"}).status_code == 200


def test_untrusted_host_header_rejected():
    with TestClient(_app()) as client:
        assert client.get("/api/data", headers={"Host": "evil.example.com"}).status_code == 400
        assert client.get("/api/data", headers={"Host": "localhost:8000"}).status_code == 200


def test_rate_limit_and_body_limit(monkeypatch):
    monkeypatch.setenv("FRIDAY_RATE_DEFAULT", "3")
    with TestClient(_app()) as client:
        codes = [client.get("/api/data").status_code for _ in range(5)]
        assert codes[:3] == [200, 200, 200] and codes[-1] == 429
    monkeypatch.setenv("FRIDAY_RATE_DEFAULT", "100")
    with TestClient(_app()) as client:
        assert client.post("/api/echo", json={"x": "y" * 5000}).status_code == 413


def test_generated_token_file_is_private(monkeypatch, tmp_path):
    monkeypatch.delenv("FRIDAY_API_TOKEN")
    monkeypatch.setattr(security, "_TOKEN", None)
    token = security.api_token()
    path = tmp_path / "api_token"
    assert path.read_text() == token and len(token) > 30
    assert security.api_token() == token


def test_safe_shell_commands_cannot_read_outside_workspace(tmp_path: Path):
    perms = Permissions(tmp_path.resolve(), settings={"network": "ask"})
    bash = build_tools()["bash"]
    assert perms.check(bash, {"command": "ls"})[0] == "allow"
    for cmd in ("cat ~/.ssh/id_rsa", "cat /etc/passwd", "ls ../..", r"type C:\Users\me\secret.txt"):
        assert perms.check(bash, {"command": cmd})[0] == "ask", cmd
    assert args_stay_in_workspace("git diff --stat", tmp_path)


def test_bypass_mode_and_root_project_are_refused(monkeypatch, tmp_path):
    from backend.app import agent_routes

    monkeypatch.setattr(agent_routes, "SESSIONS", {})
    monkeypatch.setattr(agent_routes, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(agent_routes, "PERSIST_PATH", tmp_path / "sessions.json")
    monkeypatch.delenv("FRIDAY_ALLOW_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(agent_routes.router)
    with TestClient(app) as client:
        assert client.post("/api/agent/sessions", json={"mode": "bypass"}).status_code == 403
        assert client.post("/api/agent/sessions", json={"project_root": "/"}).status_code == 422
        created = client.post("/api/agent/sessions", json={})
        assert created.status_code == 200
        sid = created.json()["id"]
        assert client.patch(f"/api/agent/sessions/{sid}", json={"mode": "bypass"}).status_code == 403
        monkeypatch.setenv("FRIDAY_ALLOW_BYPASS", "1")
        assert client.patch(f"/api/agent/sessions/{sid}", json={"mode": "bypass"}).status_code == 200

