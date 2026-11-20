import asyncio
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.agent import loop, tools as agent_tools
from backend.app.agent.router import router
from backend.app.agent_routes import require_local_agent_request
from friday_agent.tools import Context, ToolError


def run(coro):
    return asyncio.run(coro)


def test_tool_registry_has_requested_allowlist():
    assert set(agent_tools.get_tools()) == {
        "list_dir", "glob", "grep", "read_file", "edit_file", "write_file",
        "run_command", "git_status", "git_diff", "git_commit", "todo_write",
    }


def test_path_escape_blocked(tmp_path: Path):
    context = Context(root=tmp_path.resolve())
    with pytest.raises(ToolError):
        context.resolve("../secret.txt")


def test_edit_requires_unique_match(tmp_path: Path):
    target = tmp_path / "a.txt"
    target.write_text("x\nx\n", encoding="utf-8")
    context = Context(root=tmp_path.resolve())
    edit = agent_tools.get_tools()["edit_file"]
    context.read_files.add(target.resolve())
    with pytest.raises(ToolError, match="appears 2 times"):
        run(edit.run({"path": "a.txt", "old_string": "x", "new_string": "y"}, context))


def test_edit_success(tmp_path: Path):
    target = tmp_path / "a.txt"
    target.write_text("hello world", encoding="utf-8")
    context = Context(root=tmp_path.resolve())
    edit = agent_tools.get_tools()["edit_file"]
    context.read_files.add(target.resolve())
    run(edit.run({"path": "a.txt", "old_string": "world", "new_string": "friday"}, context))
    assert target.read_text(encoding="utf-8") == "hello friday"


@pytest.mark.parametrize(
    "command",
    ["rm -rf /", "Remove-Item C:\\ -Recurse -Force", "git reset --hard", "git push --force"],
)
def test_dangerous_command_blocked(tmp_path: Path, command: str):
    context = Context(root=tmp_path.resolve())
    run_command = agent_tools.get_tools()["run_command"]
    with pytest.raises(ToolError, match="denylist"):
        run(run_command.run({"command": command}, context))


def test_approval_allow_always_is_scoped_to_session():
    first = loop.get_session("session-one")
    second = loop.get_session("session-two")
    first.always_allow.add("write_file")
    assert "write_file" in first.always_allow
    assert "write_file" not in second.always_allow


def test_agent_loop_reads_files_and_logs_trajectory(tmp_path: Path, monkeypatch):
    (tmp_path / "CLAUDE.md").write_text("Project instruction", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("Agent instruction", encoding="utf-8")
    (tmp_path / "sample.txt").write_text("hello Friday", encoding="utf-8")
    trajectory = tmp_path / "trajectories.jsonl"
    monkeypatch.setattr(loop, "TRAJECTORY_PATH", trajectory)
    calls = 0
    seen_messages = []

    async def fake_chat(model, messages, tools=None):
        nonlocal calls
        seen_messages.append(messages)
        calls += 1
        if calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "read-1",
                    "function": {"name": "read_file", "arguments": {"path": "sample.txt"}},
                }],
            }
        return {"role": "assistant", "content": "The file says hello Friday."}

    monkeypatch.setattr(loop, "chat", fake_chat)

    async def collect():
        return [event async for event in loop.run_agent("Read the sample", str(tmp_path), "fake", "read-session")]

    events = run(collect())
    assert [event["type"] for event in events] == [
        "thinking", "tool_call", "tool_result", "thinking", "text", "done",
    ]
    assert "hello Friday" in events[2]["result"]
    prompt = seen_messages[0][0]["content"]
    assert "Project instruction" in prompt
    assert "Agent instruction" in prompt
    assert "sample.txt" in prompt
    record = json.loads(trajectory.read_text(encoding="utf-8").splitlines()[0])
    assert record["session_id"] == "read-session"
    assert record["events"][-1]["type"] == "done"


def test_write_tool_waits_for_approval_and_records_preview(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(loop, "TRAJECTORY_PATH", tmp_path / "trajectories.jsonl")
    calls = 0

    async def fake_chat(model, messages, tools=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "write-1",
                    "function": {
                        "name": "write_file",
                        "arguments": {"path": "new.txt", "content": "approved content"},
                    },
                }],
            }
        return {"role": "assistant", "content": "The file was written."}

    monkeypatch.setattr(loop, "chat", fake_chat)

    async def collect_and_approve():
        events = []
        async for event in loop.run_agent("Create a file", str(tmp_path), "fake", "write-session"):
            events.append(event)
            if event["type"] == "approval_request":
                assert "+approved content" in event["preview"]
                assert loop.resolve_approval(
                    "write-session", event["approval_id"], "allow_once"
                )
        return events

    events = run(collect_and_approve())
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "approved content"
    assert any(event["type"] == "approval_request" for event in events)
    assert "write_file" not in loop.SESSIONS["write-session"].always_allow


def test_agent_router_streams_typed_events_and_restricts_remote_access(monkeypatch, tmp_path: Path):
    async def fake_agent(task, project_root, model, session_id):
        yield {"type": "thinking", "step": 1}
        yield {"type": "text", "content": "Finished."}
        yield {"type": "done", "seconds": 1.0}

    monkeypatch.setattr("backend.app.agent.router.run_agent", fake_agent)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_local_agent_request] = lambda: None
    with TestClient(app) as client:
        response = client.post("/api/agent/stream", json={
            "task": "Inspect the repo",
            "project_root": str(tmp_path),
            "model": "fake",
            "session_id": "api-session",
        })
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert '"type": "session"' in response.text
    assert '"type": "thinking"' in response.text
    assert '"type": "done"' in response.text

