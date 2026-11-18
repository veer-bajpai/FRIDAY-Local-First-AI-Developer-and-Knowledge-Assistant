"""Shared test isolation: never let tests read or write the real FRIDAY data directory."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_agent_store(tmp_path_factory, monkeypatch):
    store = tmp_path_factory.mktemp("friday-data")
    try:
        from backend.app import agent_routes
    except Exception:  # backend deps not installed: nothing to isolate
        yield store
        return
    monkeypatch.setattr(agent_routes, "PERSIST_PATH", store / "agent_sessions.json")
    yield store
