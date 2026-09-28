"""Central configuration. Everything can be overridden with environment variables."""
from __future__ import annotations

import os
from pathlib import Path

def _host(value: str) -> str:
    """Ollama's own OLLAMA_HOST may be 'host:port' or '0.0.0.0:11434' - normalise it."""
    if not value.startswith("http"):
        value = "http://" + value
    return value.replace("0.0.0.0", "127.0.0.1").rstrip("/")

HOME = Path.home() / ".friday"
OLLAMA_HOST = _host(
    os.getenv("OLLAMA_HOST", os.getenv("OLLAMA_URL", "127.0.0.1:11434"))
)

# A tool-calling model is REQUIRED. llama3.2:1b/3b work but are weak at agentic work.
# Better: qwen2.5-coder:7b/14b/32b, qwen3:8b/14b, gpt-oss:20b, llama3.1:8b
DEFAULT_MODEL = os.getenv("FRIDAY_MODEL", "qwen2.5-coder:7b")

# Ollama's default context is only 4096 tokens - far too small for an agent.
NUM_CTX = int(os.getenv("FRIDAY_NUM_CTX", "16384"))
MAX_STEPS = int(os.getenv("FRIDAY_MAX_STEPS", "40"))  # tool-loop iterations per user turn
API_URL = os.getenv("FRIDAY_API_URL", "http://127.0.0.1:8000")  # your existing FastAPI (RAG)
BRAND = os.getenv("FRIDAY_COLOR", "cyan")  # terminal accent colour (any rich colour)

MODES = ["default", "acceptEdits", "plan", "bypass"]
MODE_LABELS = {
    "default": "ask before edits",
    "acceptEdits": "auto-accept edits",
    "plan": "plan mode (read-only)",
    "bypass": "bypass permissions",
}
