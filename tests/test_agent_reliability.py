"""Coding-agent reliability: encodings, line endings, workspace limits, timeouts, failure loops."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from friday_agent import engine
from friday_agent.engine import create_session, rewind, run_turn, trim_context
from friday_agent.repo_map import repo_map
from friday_agent.tools import Context, ToolError, build_tools


def run(coro):
    return asyncio.run(coro)


def test_edit_preserves_crlf_and_utf8(tmp_path: Path):
    target = tmp_path / "win.txt"
    target.write_bytes("naïve café\r\nline two\r\n".encode("utf-8"))
    ctx = Context(root=tmp_path.resolve())
    tools = build_tools()
    assert "naïve café" in run(tools["read_file"].run({"path": "win.txt"}, ctx))
    run(tools["edit_file"].run({"path": "win.txt", "old_string": "line two", "new_string": "line 2\nline 3"}, ctx))
    assert target.read_bytes() == "naïve café\r\nline 2\r\nline 3\r\n".encode("utf-8")


def test_rewind_keeps_newline_style(tmp_path: Path, monkeypatch):
    target = tmp_path / "f.txt"
    target.write_bytes(b"a\r\nb\r\n")

    async def fake_stream(model, messages, tools=None, options=None):
        n = sum(1 for m in messages if m["role"] == "assistant")
        calls = [[{"function": {"name": "read_file", "arguments": {"path": "f.txt"}}}],
                 [{"function": {"name": "edit_file", "arguments": {"path": "f.txt", "old_string": "b", "new_string": "c"}}}],
                 []][min(n, 2)]
        yield {"message": {"content": "" if calls else "ok", "tool_calls": calls}, "done": True}

    monkeypatch.setattr(engine, "chat_stream", fake_stream)
    session = create_session(tmp_path, "m", "acceptEdits")
    run(_drain(session, "edit"))
    assert target.read_bytes() == b"a\r\nc\r\n"
    rewind(session)
    assert target.read_bytes() == b"a\r\nb\r\n"


def test_glob_cannot_escape_workspace(tmp_path: Path):
    inner = tmp_path / "ws"
    inner.mkdir()
    (tmp_path / "secret.txt").write_text("x")
    ctx = Context(root=inner.resolve())
    glob_tool = build_tools()["glob"]
    with pytest.raises(ToolError):
        run(glob_tool.run({"pattern": "../*.txt"}, ctx))
    with pytest.raises(ToolError):
        run(glob_tool.run({"pattern": str(tmp_path / "*.txt")}, ctx))


async def _drain(session, prompt):
    return [event async for event in run_turn(session, prompt)]


def _looping_model(call):
    async def fake_stream(model, messages, tools=None, options=None):
        tool_results = sum(1 for m in messages if m["role"] == "tool")
        if tool_results >= 5:
            yield {"message": {"content": "giving up"}, "done": True}
            return
        yield {"message": {"tool_calls": [call]}, "done": True}
    return fake_stream


def test_identical_failing_call_is_not_repeated_forever(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(engine, "chat_stream", _looping_model(
        {"function": {"name": "read_file", "arguments": {"path": "missing.txt"}}}))
    session = create_session(tmp_path, "m")
    events = run(_drain(session, "read it"))
    results = [e["result"] for e in events if e["type"] == "tool_result"]
    assert results[2].startswith("Tool error") and "already failed 3 times" in results[3]
    assert events[-1]["type"] == "done"


def test_denied_action_is_not_asked_again_in_same_turn(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(engine, "chat_stream", _looping_model(
        {"function": {"name": "bash", "arguments": {"command": "touch x"}}}))
    session = create_session(tmp_path, "m")

    async def scenario():
        approvals = 0
        async for event in run_turn(session, "make x"):
            if event["type"] == "approval_required":
                approvals += 1
                session.pending_approvals[event["id"]].set_result("deny")
        return approvals

    assert run(scenario()) == 1
    assert not (tmp_path / "x").exists()


def test_tool_timeout_stops_hung_tool(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(engine, "TOOL_TIMEOUT", 0.2)
    tools = build_tools()
    slow = tools["list_dir"]

    async def hang(arguments, ctx):
        await asyncio.sleep(5)
        return "never"

    slow.run = hang
    monkeypatch.setattr(engine, "build_tools", lambda read_only=False: tools)
    monkeypatch.setattr(engine, "chat_stream", _looping_model({"function": {"name": "list_dir", "arguments": {}}}))
    session = create_session(tmp_path, "m")
    events = run(_drain(session, "list"))
    first = next(e for e in events if e["type"] == "tool_result")
    assert "did not finish within" in first["result"]


def test_context_trimming_keeps_recent_outputs(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(engine, "NUM_CTX", 2000)
    session = create_session(tmp_path, "m")
    for i in range(12):
        session.messages.append({"role": "tool", "tool_name": "read_file", "content": f"{i} " + "x" * 3000})
    assert trim_context(session) == 6
    tools = [m for m in session.messages if m["role"] == "tool"]
    assert tools[0]["content"].endswith("[older output trimmed to save context]")
    assert len(tools[-1]["content"]) > 3000


def test_repo_map_summarises_large_projects(tmp_path: Path):
    for d in range(30):
        (tmp_path / f"pkg{d}").mkdir()
        for f in range(20):
            (tmp_path / f"pkg{d}" / f"m{f}.py").write_text("")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("")
    summary = repo_map(tmp_path, 100)
    assert summary.startswith("600 files") and "node_modules" not in summary
    assert len(summary.splitlines()) < 130
    assert "Repository overview" in create_session(tmp_path, "m").messages[0]["content"]
