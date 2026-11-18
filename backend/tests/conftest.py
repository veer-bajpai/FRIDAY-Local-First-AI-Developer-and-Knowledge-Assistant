"""Fixtures for API tests: an isolated database and a deterministic local embedder."""
from __future__ import annotations

import hashlib
import math
import re

import pytest


def fake_vector(text: str, dim: int = 64) -> list[float]:
    vector = [0.0] * dim
    for word in re.findall(r"\w+", text.lower()):
        vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


@pytest.fixture()
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app import knowledge, main

    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "friday.db")
    monkeypatch.setenv("FRIDAY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FRIDAY_API_TOKEN", "test-token")

    async def embed(text, model):
        return fake_vector(text)

    monkeypatch.setattr(main, "embedding", embed)
    monkeypatch.setattr(knowledge, "INDEX", knowledge.VectorIndex())
    main.initialize()
    with TestClient(main.app) as client:
        yield client, main
