"""Central configuration. Everything can be overridden with environment variables."""
from __future__ import annotations

import os
import json
from pathlib import Path

def _host(value: str) -> str:
    """Ollama's own OLLAMA_HOST may be 'host:port' or '0.0.0.0:11434' - normalise it."""
    if not value.startswith("http"):
        value = "http://" + value
    return value.replace("0.0.0.0", "127.0.0.1").rstrip("/")

HOME = Path.home() / ".friday"
DEFAULT_SETTINGS = {
    "trusted_dirs": [],
    "network": "ask",
    "permissions": {"allow": [], "deny": []},
    "hooks": {},
    "theme": "midnight",
}

def _merge_settings(target: dict, source: dict) -> dict:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_settings(target[key], value)
        else:
            target[key] = value
    return target

def load_settings(root: Path) -> dict:
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    for file in (HOME / "settings.json", root / ".friday" / "settings.json"):
        try:
            _merge_settings(settings, json.loads(file.read_text(encoding="utf-8")))
        except FileNotFoundError:
            continue
        except (OSError, json.JSONDecodeError):
            continue
    return settings

def load_project_memory(root: Path) -> str:
    try:
        return (root / "FRIDAY.md").read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""

def load_custom_commands(root: Path) -> dict[str, str]:
    commands: dict[str, str] = {}
    for directory in (HOME / "commands", root / ".friday" / "commands"):
        if directory.is_dir():
            commands.update({file.stem: file.read_text(encoding="utf-8")
                             for file in directory.glob("*.md")})
    return commands

OLLAMA_HOST = _host(
    os.getenv("OLLAMA_HOST", os.getenv("OLLAMA_URL", "127.0.0.1:11434"))
)

# A tool-calling model is REQUIRED. Smaller models trade agent quality for speed.
# Better: qwen2.5-coder:7b/14b/32b, qwen3:8b/14b, gpt-oss:20b, llama3.1:8b
DEFAULT_MODEL = os.getenv("FRIDAY_MODEL", "llama3.2:latest")

DEFAULT_EMBED_MODEL = os.getenv("FRIDAY_EMBED_MODEL", "nomic-embed-text:latest")
# Comma-separated models to try, in order, when the configured model is not installed.
FALLBACK_MODELS = [m.strip() for m in os.getenv(
    "FRIDAY_FALLBACK_MODELS", "qwen2.5-coder:7b,llama3.2:latest,llama3.1:8b,qwen3:8b").split(",") if m.strip()]
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("FRIDAY_OLLAMA_TIMEOUT", "300"))  # max wait between streamed chunks

# Ollama's default context is only 4096 tokens - far too small for an agent.
NUM_CTX = int(os.getenv("FRIDAY_NUM_CTX", "8192"))
NUM_PREDICT = int(os.getenv("FRIDAY_NUM_PREDICT", "4096"))
OLLAMA_KEEP_ALIVE = os.getenv("FRIDAY_OLLAMA_KEEP_ALIVE", "30m")
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

