"""Agent sessions survive restarts with their transcript, todos and undo state, and never replay tools."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from friday_agent import engine, session_store
from friday_agent.engine import create_session, repair_dangling_tool_calls, run_turn


def run(coro):
    return asyncio.run(coro)


def _scripted(steps):
    async def fake_stream(model, messages, tools=None, options=None):
        n = sum(1 for m in messages if m["role"] == "assistant")
        step = steps[min(n, len(steps) - 1)]
        if step.get("raise"):
            raise step["raise"]
        if step.get("content"):
            yield {"message": {"content": step["content"]}}
        yield {"message": {"tool_calls": step.get("tool_calls", [])}, "done": True,
               "prompt_eval_count": 10, "eval_count": 5}
    return fake_stream


async def _collect(session, prompt):
    return [event async for event in run_turn(session, prompt)]


def test_full_transcript_round_trip(tmp_path: Path, monkeypatch):
    (tmp_path / "a.txt").write_text("hello\n")
    monkeypatch.setattr(engine, "chat_stream", _scripted([
        {"tool_calls": [{"function": {"name": "todo_write", "arguments": {
            "todos": [{"content": "read a.txt", "status": "in_progress"}]}}}]},
        {"tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a.txt"}}}]},
        {"content": "a.txt says hello"},
    ]))
    store = tmp_path / "store"
    session = create_session(tmp_path, "m")
    session.persist = lambda s: session_store.save(s, store)
    session.permissions.allow.add("todo_write")
    events = run(_collect(session, "what is in a.txt?"))
    assert events[-1]["type"] == "done" and events[-1]["status"] == "completed"

    data = json.loads((store / f"{session.id}.json").read_text())
    assert data["status"] == "completed" and data["title"] == "what is in a.txt?"
    restored = session_store.load_all(store)[0]
    assert restored.id == session.id
    assert [m["role"] for m in restored.messages[1:]] == [m["role"] for m in session.messages[1:]]
    assert restored.context.todos == [{"content": "read a.txt", "status": "in_progress"}]
    assert any(entry["tool"] == "read_file" and entry["ok"] for entry in restored.tool_log)
    assert restored.messages[0]["role"] == "system"


def test_interrupted_session_is_repaired_not_replayed(tmp_path: Path):
    store = tmp_path / "store"
    session = create_session(tmp_path, "m")
    session.messages += [
        {"role": "user", "content": "delete build dir"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "bash", "arguments": {"command": "rm -rf build"}}}]},
    ]
    session.status = "awaiting_approval"
    session_store.save(session, store)

    restored = session_store.load_all(store)[0]
    assert restored.status == "interrupted"
    tool_msgs = [m for m in restored.messages if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["content"].startswith("Not executed")
    assert "interrupted" in restored.messages[-1]["content"]
    assert not restored.pending_approvals


def test_repair_is_idempotent():
    messages = [{"role": "system", "content": "s"},
                {"role": "assistant", "tool_calls": [{"function": {"name": "a"}}, {"function": {"name": "b"}}]},
                {"role": "tool", "content": "ok"}]
    assert repair_dangling_tool_calls(messages) is True
    assert [m.get("content") for m in messages if m["role"] == "tool"][1].startswith("Not executed")
    assert repair_dangling_tool_calls(messages) is False


def test_undo_survives_restart(tmp_path: Path, monkeypatch):
    target = tmp_path / "f.txt"
    target.write_text("old\n")
    monkeypatch.setattr(engine, "chat_stream", _scripted([
        {"tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "f.txt"}}}]},
        {"tool_calls": [{"function": {"name": "edit_file", "arguments": {
            "path": "f.txt", "old_string": "old", "new_string": "new"}}}]},
        {"content": "done"},
    ]))
    store = tmp_path / "store"
    session = create_session(tmp_path, "m", "acceptEdits")
    session.persist = lambda s: session_store.save(s, store)
    run(_collect(session, "change old to new"))
    assert target.read_text() == "new\n"
    restored = session_store.load_all(store)[0]
    assert engine.rewind(restored) == ["change old to new"]
    assert target.read_text() == "old\n"


def test_failed_and_cancelled_status(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(engine, "chat_stream", _scripted([{"raise": engine.LLMError("model missing")}]))
    session = create_session(tmp_path, "m")
    events = run(_collect(session, "hi"))
    assert session.status == "failed" and session.last_error == "model missing"
    assert events[-1]["status"] == "failed"

    async def slow_stream(*args, **kwargs):
        await asyncio.sleep(10)
        yield {}

    monkeypatch.setattr(engine, "chat_stream", slow_stream)
    session = create_session(tmp_path, "m")

    async def cancel_it():
        task = asyncio.create_task(_collect(session, "hang"))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    run(cancel_it())
    assert session.status == "cancelled" and not session.busy


def test_http_resume_and_history_after_restart(tmp_path: Path, monkeypatch):
    from backend.app import agent_routes

    monkeypatch.setattr(agent_routes, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(agent_routes, "PERSIST_PATH", tmp_path / "data" / "agent_sessions.json")
    monkeypatch.setattr(agent_routes, "SESSIONS", {})
    monkeypatch.setattr(engine, "chat_stream", _scripted([{"content": "first answer"}]))

    async def no_mcp():
        return None

    app = FastAPI()
    app.include_router(agent_routes.router)
    with TestClient(app) as client:
        sid = client.post("/api/agent/sessions", json={}).json()["id"]
        with client.stream("POST", f"/api/agent/sessions/{sid}/turns", json={"prompt": "remember 42"}) as r:
            body = "".join(r.iter_text())
        assert "first answer" in body

    # simulate a backend restart: memory cleared, sessions restored from disk on startup
    monkeypatch.setattr(agent_routes, "SESSIONS", {})
    with TestClient(app) as client:
        resumed = client.post("/api/agent/sessions", json={"resume_id": sid})
        assert resumed.status_code == 200 and resumed.json()["resumable"] is True
        history = client.get(f"/api/agent/sessions/{sid}/history").json()["history"]
        assert [h["content"] for h in history] == ["remember 42", "first answer"]
        listed = client.get("/api/agent/sessions").json()
        assert listed[0]["title"] == "remember 42" and listed[0]["status"] == "completed"
        assert client.post("/api/agent/sessions", json={"resume_id": "doesnotexist"}).status_code == 404

