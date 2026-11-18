"""Hybrid retrieval, attribution, filtering, dedup, index invalidation and long-term memory."""
from __future__ import annotations

import asyncio
import io
import sqlite3

from backend.app import knowledge


def _upload(client, name: str, text: str):
    response = client.post("/api/documents/upload", files={"file": (name, io.BytesIO(text.encode()), "text/plain")})
    assert response.status_code == 202, response.text
    doc = client.get(f"/api/documents/{response.json()['id']}").json()
    assert doc["status"] == "ready", doc
    return doc


def test_hybrid_search_ranks_and_attributes(api):
    client, _ = api
    _upload(client, "kiwi.md", "Kiwi birds live in New Zealand and cannot fly.\n\nThey are nocturnal.")
    rust = _upload(client, "rust.md", "The Rust borrow checker enforces ownership and lifetimes at compile time.")
    results = client.post("/api/search", json={"query": "how does the borrow checker work", "k": 3}).json()["results"]
    assert results[0]["document_id"] == rust["id"]
    assert results[0]["title"] == "rust.md" and results[0]["chunk"] == 0
    assert results[0]["method"] in {"hybrid", "lexical", "semantic"}
    assert 0 <= results[0]["score"] <= 1


def test_document_filter_collection_and_delete_invalidate_index(api):
    client, _ = api
    a = _upload(client, "a.txt", "alpha protocol handshake timeout settings")
    b = _upload(client, "b.txt", "alpha protocol retry backoff settings")
    only_b = client.post("/api/search", json={"query": "alpha protocol", "document_ids": [b["id"]]}).json()["results"]
    assert {r["document_id"] for r in only_b} == {b["id"]}

    collection = client.post("/api/collections", json={"title": "proto"}).json()
    client.post(f"/api/collections/{collection['id']}/documents/{a['id']}")
    in_collection = client.post("/api/search", json={"query": "alpha", "collection_id": collection["id"]}).json()
    assert {r["document_id"] for r in in_collection["results"]} == {a["id"]}

    assert client.delete(f"/api/documents/{a['id']}").status_code == 200
    after = client.post("/api/search", json={"query": "handshake timeout"}).json()["results"]
    assert all(r["document_id"] != a["id"] for r in after)


def test_duplicate_chunks_are_not_stored_twice(api):
    client, main = api
    paragraph = "Repeated paragraph about telemetry pipelines. " * 30
    doc = _upload(client, "dup.txt", paragraph + "\n\n" + paragraph)
    with main.db() as connection:
        hashes = [r[0] for r in connection.execute("SELECT content_hash FROM chunks WHERE document_id = ?", (doc["id"],))]
    assert len(hashes) == len(set(hashes))


def test_vector_index_is_cached_until_bumped(api):
    client, main = api
    _upload(client, "x.txt", "zebra stripes pattern")
    index = knowledge.INDEX
    with main.db() as connection:
        index.search(connection, [1.0] * 64, 5)
        built = index._matrix
        index.search(connection, [1.0] * 64, 5)
        assert index._matrix is built  # no rebuild between queries
        index.bump()
        index.search(connection, [1.0] * 64, 5)
        assert index._matrix is not built


def test_memory_crud_and_chat_injection(api):
    client, main = api
    created = client.post("/api/memories", json={"content": "My favourite editor is VS Code", "kind": "preference"})
    assert created.status_code == 201
    memory = created.json()
    assert client.post("/api/memories", json={"content": "my favourite editor is vs code"}).json()["id"] == memory["id"]
    listed = client.get("/api/memories").json()
    assert [m["content"] for m in listed] == ["My favourite editor is VS Code"]
    found = client.get("/api/memories", params={"q": "which editor do I like"}).json()
    assert found and found[0]["id"] == memory["id"]

    request = main.ChatRequest(question="Which editor should I configure?")
    messages, _, memories = asyncio.run(main.build_chat_messages(request, main.get_settings(), []))
    assert memories and "VS Code" in messages[0]["content"]

    updated = client.patch(f"/api/memories/{memory['id']}", json={"content": "Favourite editor: Neovim"}).json()
    assert updated["content"] == "Favourite editor: Neovim"
    assert client.delete(f"/api/memories/{memory['id']}").status_code == 200
    assert client.delete(f"/api/memories/{memory['id']}").status_code == 404


def test_chunking_and_remember_parsing():
    text = "\n\n".join(f"para {i} " + "word " * 100 for i in range(6))
    chunks = knowledge.chunk_text(text, words=220, overlap=40)
    assert len(chunks) >= 3 and all(len(c.split()) <= 260 for c in chunks)
    assert knowledge.parse_remember("remember: I deploy on Fridays") == "I deploy on Fridays"
    assert knowledge.parse_remember("/remember use tabs") == "use tabs"
    assert knowledge.parse_remember("do you remember the plan?") is None


def test_migration_upgrades_old_database(tmp_path):
    connection = sqlite3.connect(tmp_path / "old.db")
    connection.executescript("""
        CREATE TABLE documents (id TEXT PRIMARY KEY, name TEXT, file_path TEXT, file_type TEXT,
            size_bytes INTEGER, status TEXT, error TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE chunks (id INTEGER PRIMARY KEY AUTOINCREMENT, document_id TEXT, chunk_index INTEGER,
            content TEXT, embedding TEXT);
        INSERT INTO documents VALUES ('d', 'old.txt', 'p', 'txt', 1, 'ready', NULL, 't', 't');
        INSERT INTO chunks(document_id, chunk_index, content) VALUES ('d', 0, 'legacy gearbox manual');
    """)
    assert knowledge.migrate(connection)
    connection.row_factory = sqlite3.Row
    results = knowledge.retrieve(connection, "gearbox", None, 3)
    assert results and results[0]["title"] == "old.txt"
