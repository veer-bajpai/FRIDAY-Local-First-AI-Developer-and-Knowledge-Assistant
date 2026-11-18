"""Chat streaming against a fake Ollama: real token forwarding, fallback, errors, stop, memory."""
from __future__ import annotations

import json

import pytest

from friday_agent import llm


def _events(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.fixture()
def ollama(api, monkeypatch):
    client, main = api
    state = {"models": ["llama3.2:latest", "nomic-embed-text:latest"], "tokens": ["Hel", "lo", " Boss"],
             "fail_after": None, "error": None, "calls": []}

    async def installed(force=False):
        return state["models"]

    async def fake_stream(model, messages, tools=None, options=None):
        state["calls"].append({"model": model, "messages": messages, "options": options})
        if state["error"]:
            raise llm.LLMError(state["error"])
        for index, token in enumerate(state["tokens"]):
            if state["fail_after"] is not None and index == state["fail_after"]:
                raise llm.LLMError("The connection to Ollama was lost mid-response (was Ollama stopped?).")
            yield {"message": {"content": token}}
        yield {"message": {"content": ""}, "done": True, "done_reason": "stop"}

    monkeypatch.setattr(llm, "installed_models", installed)
    monkeypatch.setattr(main, "installed_models", installed)
    monkeypatch.setattr(main, "llm_chat_stream", fake_stream)
    return client, main, state


def test_tokens_are_forwarded_individually_and_saved(ollama):
    client, main, state = ollama
    response = client.post("/api/chat/stream", json={"question": "hi"})
    events = _events(response)
    assert events[0]["meta"]["model"] == "llama3.2:latest"
    assert [e["token"] for e in events if "token" in e] == ["Hel", "lo", " Boss"]
    done = events[-1]
    assert done["done"] and done["model"] == "llama3.2:latest"
    assert state["calls"][0]["options"]["num_predict"] == main.OLLAMA_MAX_TOKENS >= 1024
    history = client.get(f"/api/conversations/{done['conversation_id']}").json()
    assert [m["content"] for m in history["messages"]] == ["hi", "Hello Boss"]


def test_missing_model_falls_back_with_note(ollama):
    client, main, state = ollama
    state["models"] = ["qwen2.5-coder:7b", "nomic-embed-text:latest"]
    events = _events(client.post("/api/chat/stream", json={"question": "hi"}))
    assert events[0]["meta"]["model"] == "qwen2.5-coder:7b"
    assert "not installed" in events[0]["meta"]["note"]
    state["models"] = ["nomic-embed-text:latest"]
    events = _events(client.post("/api/chat/stream", json={"question": "hi"}))
    assert "No chat model is installed" in events[-1]["error"]


def test_ollama_offline_gives_clear_error_and_no_junk_history(ollama):
    client, main, state = ollama
    state["models"] = None
    events = _events(client.post("/api/chat/stream", json={"question": "hi"}))
    assert "Cannot reach Ollama" in events[-1]["error"]
    assert client.get("/api/conversations").json() == []
    response = client.post("/api/chat", json={"question": "hi"})
    assert response.status_code == 503 and "Cannot reach Ollama" in response.json()["detail"]
    assert client.get("/api/conversations").json() == []


def test_mid_stream_failure_keeps_partial_answer(ollama):
    client, main, state = ollama
    state["fail_after"] = 2
    events = _events(client.post("/api/chat/stream", json={"question": "hi"}))
    assert [e["token"] for e in events if "token" in e] == ["Hel", "lo"]
    assert "lost mid-response" in events[-1]["error"]
    conversation = client.get(f"/api/conversations/{events[-1]['conversation_id']}").json()
    assert conversation["messages"][-1]["content"].startswith("Hello")
    assert "interrupted" in conversation["messages"][-1]["content"]


def test_remember_command_stores_memory_without_calling_model(ollama):
    client, main, state = ollama
    events = _events(client.post("/api/chat/stream", json={"question": "remember: I deploy on Fridays"}))
    assert "I deploy on Fridays" in events[1]["token"] and events[-1]["done"]
    assert state["calls"] == []
    assert client.get("/api/memories").json()[0]["content"] == "I deploy on Fridays"
    client.post("/api/chat/stream", json={"question": "when do I deploy?"})
    assert "I deploy on Fridays" in state["calls"][0]["messages"][0]["content"]


def test_non_streaming_chat_and_settings_validation(ollama):
    client, main, state = ollama
    body = client.post("/api/chat", json={"question": "hi"}).json()
    assert body["answer"] == "Hello Boss" and body["model"] == "llama3.2:latest"
    assert client.put("/api/settings", json={"model": "not-a-model"}).status_code == 422
    assert client.put("/api/settings", json={"model": "llama3.2"}).status_code == 200


def test_resolve_model_prefers_configured_fallbacks(monkeypatch):
    import asyncio

    async def installed(force=False):
        return ["mxbai-embed-large:latest", "phi3:mini", "qwen2.5-coder:7b"]

    monkeypatch.setattr(llm, "installed_models", installed)
    assert asyncio.run(llm.resolve_model("llama3.2")) [0] == "qwen2.5-coder:7b"
    assert asyncio.run(llm.resolve_model("phi3:mini")) == ("phi3:mini", None)
