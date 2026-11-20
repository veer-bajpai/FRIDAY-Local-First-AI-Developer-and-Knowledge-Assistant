"""Shared test isolation: never let tests read or write the real FRIDAY data directory."""
from __future__ import annotations

import os
import tempfile

import pytest

# Set before any backend module is imported, so module-level paths (DB, token, sessions)
# point at a throwaway folder instead of the project's real ./data.
os.environ["FRIDAY_DATA_DIR"] = tempfile.mkdtemp(prefix="friday-test-data-")


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

