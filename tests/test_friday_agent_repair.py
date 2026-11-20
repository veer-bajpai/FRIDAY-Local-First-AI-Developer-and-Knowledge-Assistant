from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from friday_agent.hooks import Hooks
from friday_agent.insights import build_report
from friday_agent.git_ops import _revision
from friday_agent.tools import ToolError
from backend.app.agent.router import _validate_project_root


def test_hooks_noop_without_configuration(tmp_path: Path):
    hooks = Hooks({}, tmp_path)
    assert hooks.run("PreToolUse", "read_file", {"path": "README.md"}) == (True, "")
    assert hooks.run("PostToolUse", "read_file", {"path": "README.md"}, "ok") is None


def test_hooks_validate_configuration(tmp_path: Path):
    with pytest.raises(ValueError, match="Unsupported"):
        Hooks({"Unknown": []}, tmp_path)
    with pytest.raises(TypeError, match="mapping"):
        Hooks({"PreToolUse": "invalid"}, tmp_path)


def test_build_report_is_real_local_html(tmp_path: Path):
    (tmp_path / ".friday").mkdir()
    (tmp_path / ".friday" / "usage.jsonl").write_text(
        '{"tool":"read_file","ok":true}\n{"tool":"git_status","ok":true}\n',
        encoding="utf-8",
    )
    report = build_report(tmp_path)
    assert report.is_file()
    content = report.read_text(encoding="utf-8")
    assert "FRIDAY Agent Insights" in content
    assert "read_file" in content
    assert "git_status" in content


def test_git_revision_rejects_option_injection():
    with pytest.raises(ToolError, match="Invalid Git revision"):
        _revision("--output=outside.txt")
    assert _revision("HEAD~1") == "HEAD~1"


def test_legacy_agent_rejects_home_as_workspace(tmp_path: Path):
    with pytest.raises(HTTPException, match="home folder"):
        _validate_project_root(str(Path.home()))
    assert _validate_project_root(str(tmp_path)) == tmp_path.resolve()


def test_hooks_support_legacy_glob_matcher_and_block(tmp_path: Path):
    import sys

    blocker = tmp_path / "block.py"
    blocker.write_text("import sys, json; d = json.load(sys.stdin); print('no ' + d['args']['command']); sys.exit(2)\n")
    hooks = Hooks({"PreToolUse": [{"matcher": "ba*", "command": f'"{sys.executable}" "{blocker}"'}]}, tmp_path)
    allowed, reason = hooks.run("PreToolUse", "bash", {"command": "ls"})
    assert allowed is False and "no ls" in reason
    assert hooks.run("PreToolUse", "read_file", {"path": "x"}) == (True, "")


def test_resume_loads_a_saved_session_that_is_not_in_memory(tmp_path: Path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app import agent_routes

    monkeypatch.setattr(agent_routes, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(agent_routes, "PERSIST_PATH", tmp_path / "data" / "agent_sessions.json")
    monkeypatch.setattr(agent_routes, "SESSIONS", {})
    app = FastAPI()
    app.include_router(agent_routes.router)
    with TestClient(app) as client:
        sid = client.post("/api/agent/sessions", json={}).json()["id"]
        agent_routes.SESSIONS[sid].save()
        agent_routes.SESSIONS.clear()  # evicted from memory, still on disk
        resumed = client.post("/api/agent/sessions", json={"resume_id": sid})
        assert resumed.status_code == 200 and resumed.json()["id"] == sid
        assert client.post("/api/agent/sessions", json={"resume_id": "doesnotexist"}).status_code == 404


def test_agent_session_model_can_be_switched_to_an_installed_model(tmp_path: Path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app import agent_routes

    async def installed():
        return ["llama3.2:latest", "qwen2.5-coder:7b"]

    monkeypatch.setattr(agent_routes, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(agent_routes, "SESSIONS", {})
    monkeypatch.setattr(agent_routes, "list_models", installed)
    app = FastAPI()
    app.include_router(agent_routes.router)
    with TestClient(app) as client:
        sid = client.post("/api/agent/sessions", json={"model": "llama3.2:latest"}).json()["id"]
        switched = client.patch(f"/api/agent/sessions/{sid}", json={"model": "qwen2.5-coder:7b"})
        assert switched.status_code == 200 and switched.json()["model"] == "qwen2.5-coder:7b"
        assert agent_routes.SESSIONS[sid].model == "qwen2.5-coder:7b"
        assert client.patch(f"/api/agent/sessions/{sid}", json={"model": "llama3.2"}).json()["model"] == "llama3.2:latest"
        missing = client.patch(f"/api/agent/sessions/{sid}", json={"model": "gpt-4"})
        assert missing.status_code == 422 and "not installed" in missing.json()["detail"]
        assert client.patch(f"/api/agent/sessions/{sid}", json={"mode": "plan"}).json()["mode"] == "plan"
        assert client.patch(f"/api/agent/sessions/{sid}", json={}).status_code == 422


def test_terminal_model_command_lists_and_switches(monkeypatch):
    import asyncio

    from friday_agent.terminal import FridayAgentApp

    app = FridayAgentApp(api="http://127.0.0.1:9")
    app.session_id = "abc123"
    app.model = "llama3.2:latest"
    calls, lines = [], []

    async def fake_json(method, url, body=None):
        calls.append((method, url, body))
        if method == "GET":
            return {"models": ["qwen2.5-coder:7b", "llama3.2:latest"]}
        return {"model": body["model"]}

    monkeypatch.setattr(app, "_json", fake_json)
    monkeypatch.setattr(app, "_write_system", lines.append)
    monkeypatch.setattr(app, "_render_hud", lambda: None)
    monkeypatch.setattr(app, "_render_side", lambda: None)
    asyncio.run(app._switch_model(""))
    assert any("● " in line and "llama3.2:latest" in line for line in lines)
    asyncio.run(app._switch_model("2"))
    assert calls[-1][0] == "PATCH" and calls[-1][2] == {"model": "qwen2.5-coder:7b"}
    assert app.model == "qwen2.5-coder:7b"

