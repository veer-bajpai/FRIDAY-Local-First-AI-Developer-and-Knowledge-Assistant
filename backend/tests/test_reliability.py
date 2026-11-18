"""Health checks, request IDs, error handling, interrupted ingestion and connection hygiene."""
from __future__ import annotations

import io
import sqlite3

from friday_agent import llm


def test_liveness_and_deep_health(api, monkeypatch):
    client, main = api

    async def offline(force=False):
        return None

    monkeypatch.setattr(main, "installed_models", offline)
    assert client.get("/health").json()["status"] == "ok"
    health = client.get("/api/health").json()
    assert health["status"] == "degraded"
    assert health["checks"]["database"]["status"] == "ok" and health["checks"]["database"]["fulltext_index"]
    assert health["checks"]["ollama"]["status"] == "error"
    assert health["checks"]["agent"]["store_writable"] is True

    async def online(force=False):
        return ["llama3.2:latest", "nomic-embed-text:latest"]

    monkeypatch.setattr(main, "installed_models", online)
    assert client.get("/api/health").json()["status"] in {"ok", "degraded"}  # degraded only if MCP/agent issue


def test_request_id_and_safe_500(api):
    client, main = api

    @main.app.get("/api/__boom")
    def boom():
        raise RuntimeError("secret internals")

    from fastapi.testclient import TestClient

    with TestClient(main.app, raise_server_exceptions=False) as raw:
        response = raw.get("/api/__boom", headers={"X-Request-ID": "abc123"})
    assert response.status_code == 500
    assert "secret internals" not in response.text
    assert response.json()["request_id"] == "abc123"
    assert client.get("/api/documents").headers["X-Request-ID"]


def test_interrupted_ingestion_is_resumed_and_reprocess(api):
    client, main = api
    doc = client.post("/api/documents/upload",
                      files={"file": ("n.md", io.BytesIO(b"falcon telemetry notes"), "text/markdown")}).json()
    with main.db() as connection:
        connection.execute("UPDATE documents SET status = 'processing' WHERE id = ?", (doc["id"],))
        connection.execute("DELETE FROM chunks WHERE document_id = ?", (doc["id"],))
    from fastapi.testclient import TestClient

    with TestClient(main.app):  # startup re-queues stuck documents
        pass
    import time
    for _ in range(50):
        if client.get(f"/api/documents/{doc['id']}").json()["status"] == "ready":
            break
        time.sleep(0.05)
    assert client.get(f"/api/documents/{doc['id']}").json()["chunks"] == 1
    assert client.post(f"/api/documents/{doc['id']}/reprocess").status_code == 202
    assert client.post("/api/documents/nope/reprocess").status_code == 404


def test_db_connections_are_closed(api):
    client, main = api
    with main.db() as connection:
        connection.execute("SELECT 1")
    try:
        connection.execute("SELECT 1")
        closed = False
    except sqlite3.ProgrammingError:
        closed = True
    assert closed


def test_llm_stream_reports_inline_errors(monkeypatch):
    import asyncio

    import httpx

    def handler(request):
        return httpx.Response(200, text='{"message":{"content":"a"}}\n{"error":"model runner crashed"}\n')

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))

    async def consume():
        return [chunk async for chunk in llm.chat_stream("m", [])]

    try:
        asyncio.run(consume())
        raised = None
    except llm.LLMError as exc:
        raised = str(exc)
    assert raised and "model runner crashed" in raised
