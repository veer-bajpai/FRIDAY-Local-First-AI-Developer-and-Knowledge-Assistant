"""The standalone conversation API has been removed; agent and knowledge APIs remain."""
from __future__ import annotations


def test_standalone_chat_and_conversation_routes_are_removed(api):
    client, main = api

    assert client.post("/api/chat", json={"question": "hello"}).status_code == 404
    assert client.post("/api/chat/stream", json={"question": "hello"}).status_code == 404
    assert client.get("/api/conversations").status_code == 404
    assert client.get("/api/models").status_code == 404
    assert client.get("/api/status").status_code == 404

    with main.db() as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "conversations" not in tables
    assert "messages" not in tables


def test_browser_and_agent_api_remain_available(api):
    client, _ = api

    assert client.get("/api/documents").status_code == 200
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/agent/sessions").status_code == 200


def test_embedding_settings_still_validate(api, monkeypatch):
    from friday_agent import llm

    client, _ = api

    async def installed(force=False):
        return ["nomic-embed-text:latest", "llama3.2:latest"]

    monkeypatch.setattr(llm, "installed_models", installed)
    assert client.put("/api/settings", json={"embed_model": "not-a-model"}).status_code == 422
    response = client.put("/api/settings", json={"embed_model": "nomic-embed-text"})
    assert response.status_code == 200
    assert response.json()["embed_model"] == "nomic-embed-text"