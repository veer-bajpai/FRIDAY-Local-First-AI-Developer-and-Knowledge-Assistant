"""Friday Code (terminal) reliability: tool routing, unanswered-question recovery, loops, cancellation.

These use a fake model stream, so they run without Ollama. The failure shapes come from real
llama3.2 runs (see docs/friday-code-report.md): JSON tool calls printed as text, calls to tools
that do not exist, malformed JSON calls, and empty replies.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from friday_agent import engine
from friday_agent.engine import create_session, run_turn, window_messages
from friday_agent.routing import (looks_like_broken_call, split_trailing_call, looks_like_tool_json, parse_text_tool_call,
                                  tool_profile, wants_tools)


def run(coro):
    return asyncio.run(coro)


async def _drain(session, prompt):
    return [event async for event in run_turn(session, prompt)]


def _scripted(replies, seen):
    """Fake chat_stream returning scripted replies in order; records (tools, messages) per call."""
    replies = list(replies)

    async def fake_stream(model, messages, tools=None, options=None):
        seen.append({"tools": [t["function"]["name"] for t in tools or []], "messages": list(messages)})
        reply = replies.pop(0)
        if isinstance(reply, dict):
            yield {"message": reply, "done": True, "prompt_eval_count": 10, "eval_count": 3,
                   "prompt_eval_duration": 1_000_000, "eval_duration": 3_000_000}
            return
        for piece in [reply[i:i + 4] for i in range(0, len(reply), 4)] or [""]:
            yield {"message": {"content": piece}, "done": False}
        yield {"message": {"content": ""}, "done": True, "prompt_eval_count": 10, "eval_count": 3,
               "prompt_eval_duration": 1_000_000, "eval_duration": 3_000_000}
    return fake_stream


def _session(tmp_path, routing="auto"):
    session = create_session(tmp_path, "m")
    session.tool_routing = routing
    return session


def _text(events):
    return "".join(e["content"] for e in events if e["type"] == "token")


# ----------------------------------------------------------------- routing


def test_routing_keeps_plain_questions_tool_free():
    for prompt in ("What is 2 + 2?", "Explain closures in JavaScript",
                   "Write a Python function is_palindrome(s)", "Write merge sort in Python",
                   "How do I run a Python script?", "and/or vs input/output"):
        assert wants_tools(prompt)[0] is False, prompt


def test_routing_sends_tools_for_workspace_and_actions():
    assert tool_profile("What does friday_agent/llm.py do?")[0] == "read"
    assert tool_profile("Which file in this project defines the client?")[0] == "read"
    assert tool_profile("show git status")[0] == "read"
    assert tool_profile("Run the tests")[0] == "full"
    assert tool_profile("Create a file hello.py that prints hi")[0] == "full"
    assert tool_profile("commit these changes")[0] == "full"
    assert tool_profile("What timeout does that use?", previous_used_tools=True)[0] == "read"


def test_text_tool_call_parsing():
    assert parse_text_tool_call('{"name":"read_file","parameters":{"path":"a.py"}}') == {
        "name": "read_file", "arguments": {"path": "a.py"}}
    assert parse_text_tool_call('```json\n{"name": "glob", "arguments": {"pattern": "*"}}\n```')["name"] == "glob"
    assert parse_text_tool_call('{"name": "x"} trailing prose') is None
    assert parse_text_tool_call('{"a": 1, "b": 2}') is None
    assert looks_like_broken_call('{"name":"glob","parameters{"path":"x"}}')
    assert not looks_like_broken_call('{"name":"glob","parameters":{}}')
    assert looks_like_tool_json(" {") and looks_like_tool_json("```") and not looks_like_tool_json("Hello")


# ----------------------------------------------------------------- engine behaviour


def test_plain_question_is_sent_without_tool_schemas(tmp_path: Path, monkeypatch):
    seen = []
    monkeypatch.setattr(engine, "chat_stream", _scripted(["4"], seen))
    events = run(_drain(_session(tmp_path), "What is 2 + 2?"))
    assert seen[0]["tools"] == []
    assert _text(events) == "4"
    metrics = next(e for e in events if e["type"] == "metrics")
    assert metrics["tools"] == "none" and metrics["status"] == "completed" and metrics["ttft_s"] is not None
    assert [e["type"] for e in events][-2:] == ["metrics", "done"]


def test_browser_sessions_keep_tools_on_every_step(tmp_path: Path, monkeypatch):
    seen = []
    monkeypatch.setattr(engine, "chat_stream", _scripted(["4"], seen))
    run(_drain(create_session(tmp_path, "m"), "What is 2 + 2?"))  # default routing = "always"
    assert len(seen[0]["tools"]) > 10


def test_json_tool_call_printed_as_text_is_executed(tmp_path: Path, monkeypatch):
    (tmp_path / "a.py").write_text("TIMEOUT = 300\n")
    seen = []
    monkeypatch.setattr(engine, "chat_stream", _scripted(
        ['{"name": "read_file", "parameters": {"path": "a.py"}}', "It uses 300 seconds."], seen))
    session = _session(tmp_path)
    events = run(_drain(session, "What timeout does a.py set?"))
    assert seen[0]["tools"] and "read_file" in seen[0]["tools"]
    results = [e for e in events if e["type"] == "tool_result"]
    assert results and "TIMEOUT = 300" in results[0]["result"]
    assert _text(events) == "It uses 300 seconds."  # the JSON never reached the user
    assert '{"name"' not in _text(events)


def test_unknown_tool_json_is_retried_not_shown(tmp_path: Path, monkeypatch):
    seen = []
    monkeypatch.setattr(engine, "chat_stream", _scripted(['{"name":"add","parameters":{"a":2,"b":2}}', "4"], seen))
    session = _session(tmp_path)
    events = run(_drain(session, "What is 2 + 2?"))
    assert _text(events) == "4"
    assert any(e["type"] == "status" for e in events)
    assert "does not exist" in seen[1]["messages"][-1]["content"]          # nudge sent once...
    assert all("does not exist" not in str(m.get("content")) for m in session.messages)  # ...never stored
    assert session.messages[-1] == {"role": "assistant", "content": "4"}


def test_malformed_tool_call_is_retried(tmp_path: Path, monkeypatch):
    seen = []
    monkeypatch.setattr(engine, "chat_stream", _scripted(
        ['{"name":"glob","parameters{"pattern":"x"}}', "The file is friday_agent/llm.py."], seen))
    events = run(_drain(_session(tmp_path), "Which file in this project defines the client?"))
    assert _text(events) == "The file is friday_agent/llm.py."


def test_empty_reply_is_retried_then_reported_never_faked(tmp_path: Path, monkeypatch):
    seen = []
    monkeypatch.setattr(engine, "chat_stream", _scripted(["", "Hello!"], seen))
    events = run(_drain(_session(tmp_path), "hi"))
    assert _text(events) == "Hello!"

    seen = []
    monkeypatch.setattr(engine, "chat_stream", _scripted(["", "  ", ""], seen))
    session = _session(tmp_path)
    events = run(_drain(session, "hi"))
    assert _text(events) == ""
    errors = [e for e in events if e["type"] == "error"]
    assert errors and "empty response" in errors[0]["message"]
    assert session.status == "failed"
    assert events[-1]["type"] == "done" and events[-1]["status"] == "failed"
    assert session.messages[-1]["role"] == "system"  # nothing invented; the failed prompt is left out
    assert all(m.get("role") != "assistant" for m in session.messages)


def test_misrouted_question_escalates_to_tools(tmp_path: Path, monkeypatch):
    (tmp_path / "notes.txt").write_text("alpha")
    seen = []
    monkeypatch.setattr(engine, "chat_stream", _scripted(
        ['{"name":"read_file","parameters":{"path":"notes.txt"}}',
         {"tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "notes.txt"}}}]},
         "It says alpha."], seen))
    events = run(_drain(_session(tmp_path), "what does it say"))
    assert seen[0]["tools"] == [] and len(seen[1]["tools"]) > 10
    assert _text(events) == "It says alpha."


def test_json_answer_requested_by_user_is_still_shown(tmp_path: Path, monkeypatch):
    seen = []
    monkeypatch.setattr(engine, "chat_stream", _scripted(['{"city": "Paris", "country": "France"}'], seen))
    events = run(_drain(_session(tmp_path), "Give me a JSON object with a city and country"))
    assert json.loads(_text(events)) == {"city": "Paris", "country": "France"}


def test_repeated_successful_call_is_stopped(tmp_path: Path, monkeypatch):
    (tmp_path / "a.txt").write_text("x")
    call = {"function": {"name": "read_file", "arguments": {"path": "a.txt"}}}

    async def looping(model, messages, tools=None, options=None):
        if sum(1 for m in messages if m["role"] == "tool") >= 4:
            yield {"message": {"content": "done"}, "done": True}
            return
        yield {"message": {"tool_calls": [call]}, "done": True}

    monkeypatch.setattr(engine, "chat_stream", looping)
    events = run(_drain(create_session(tmp_path, "m"), "read a.txt"))
    results = [e["result"] for e in events if e["type"] == "tool_result"]
    assert not results[0].startswith("Tool error") and not results[1].startswith("Tool error")
    assert "already ran" in results[2]


def test_window_keeps_system_prompt_and_latest_exchange(monkeypatch):
    monkeypatch.setattr(engine, "NUM_CTX", 2000)
    monkeypatch.setattr(engine, "NUM_PREDICT", 500)
    messages = [{"role": "system", "content": "SYS"}]
    for i in range(30):
        messages += [{"role": "user", "content": f"q{i} " + "x" * 300},
                     {"role": "assistant", "content": f"a{i} " + "y" * 300}]
    sent, dropped = window_messages(messages)
    assert sent[0]["content"] == "SYS" and sent[1]["role"] == "user"
    assert sent[-1]["content"].startswith("a29") and dropped > 0
    assert len(json.dumps(sent)) * 2 // 7 <= 2000 - 500


# ----------------------------------------------------------------- API: routing, cancel


def _api(tmp_path, monkeypatch):
    from backend.app import agent_routes

    monkeypatch.setattr(agent_routes, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(agent_routes, "PERSIST_PATH", tmp_path / "data" / "agent_sessions.json")
    monkeypatch.setattr(agent_routes, "SESSIONS", {})
    app = FastAPI()
    app.include_router(agent_routes.router)
    return agent_routes, app


def test_terminal_session_requests_auto_routing(tmp_path: Path, monkeypatch):
    agent_routes, app = _api(tmp_path, monkeypatch)
    with TestClient(app) as client:
        body = client.post("/api/agent/sessions", json={"model": "m", "tool_routing": "auto"}).json()
        assert body["tool_routing"] == "auto"
        sid = body["id"]
        assert client.patch(f"/api/agent/sessions/{sid}", json={"tool_routing": "never"}).json()["tool_routing"] == "never"
        assert client.post("/api/agent/sessions", json={"model": "m"}).json()["tool_routing"] == "always"
        assert client.post("/api/agent/sessions", json={"tool_routing": "bogus"}).status_code == 422


def test_cancel_stops_running_turn_and_frees_session(tmp_path: Path, monkeypatch):
    agent_routes, app = _api(tmp_path, monkeypatch)
    stopped = threading.Event()

    async def slow_stream(model, messages, tools=None, options=None):
        try:
            yield {"message": {"content": "partial "}, "done": False}
            await asyncio.sleep(30)  # a long generation
            yield {"message": {"content": "never"}, "done": True}
        finally:
            stopped.set()  # the model request was closed

    monkeypatch.setattr(engine, "chat_stream", slow_stream)
    with TestClient(app) as client:
        sid = client.post("/api/agent/sessions", json={"model": "m", "tool_routing": "auto"}).json()["id"]
        received: list[str] = []

        def consume():
            with client.stream("POST", f"/api/agent/sessions/{sid}/turns", json={"prompt": "write an essay"}) as r:
                for line in r.iter_lines():
                    received.append(line)

        reader = threading.Thread(target=consume, daemon=True)
        reader.start()
        deadline = time.time() + 5
        while not any("partial" in line for line in received) and time.time() < deadline:
            time.sleep(0.02)
        started = time.perf_counter()
        result = client.post(f"/api/agent/sessions/{sid}/cancel").json()
        assert result["cancelled"] is True and result["busy"] is False
        assert time.perf_counter() - started < 5
        assert stopped.wait(5)
        reader.join(5)
        session = agent_routes.SESSIONS[sid]
        assert session.status == "cancelled" and not session.busy
        # the partial reply closes the cancelled exchange, so the next question stands alone
        assert session.messages[-1]["role"] == "assistant"
        assert session.messages[-1]["content"].startswith("partial") and "interrupted" in session.messages[-1]["content"]
        assert client.post(f"/api/agent/sessions/{sid}/cancel").json()["cancelled"] is False


def test_narration_ending_in_json_call_runs_the_call(tmp_path: Path, monkeypatch):
    (tmp_path / "llm.py").write_text("OLLAMA_TIMEOUT = 300\n")
    narrated = ('I will search for it.\n\n{"name": "grep", "parameters": {"pattern": "OLLAMA_TIMEOUT"}}')
    seen = []
    monkeypatch.setattr(engine, "chat_stream", _scripted([narrated, "It is in llm.py."], seen))
    session = _session(tmp_path)
    events = run(_drain(session, "Which file in this project sets OLLAMA_TIMEOUT?"))
    assert [e["tool"] for e in events if e["type"] == "tool_result"] == ["grep"]
    assert _text(events).endswith("It is in llm.py.")
    stored = [m for m in session.messages if m.get("tool_calls")]
    assert stored and stored[0]["content"] == "I will search for it."


def test_split_trailing_call():
    prose, call = split_trailing_call('Let me look.\n```json\n{"name": "glob", "parameters": {"pattern": "*.py"}}\n```')
    assert prose == "Let me look." and call == {"name": "glob", "arguments": {"pattern": "*.py"}}
    assert split_trailing_call("Use {braces} in f-strings.")[1] is None
    assert split_trailing_call('Example: {"a": 1}')[1] is None


def test_echoed_tool_schema_is_retried_not_shown(tmp_path: Path, monkeypatch):
    echo = ('{"type":"function","function":{"name":"glob","description":"Find files",'
            '"parameters":{"type":"object","properties":{"pattern":{"type":"string"}}}}}')
    assert parse_text_tool_call(echo) is None and looks_like_broken_call(echo)
    wrapped = '{"type":"function","function":{"name":"glob","arguments":{"pattern":"*.py"}}}'
    assert parse_text_tool_call(wrapped) == {"name": "glob", "arguments": {"pattern": "*.py"}}
    seen = []
    monkeypatch.setattr(engine, "chat_stream", _scripted([echo, "It is friday_agent/llm.py."], seen))
    events = run(_drain(_session(tmp_path), "Which file in this project defines the client?"))
    assert _text(events) == "It is friday_agent/llm.py."
