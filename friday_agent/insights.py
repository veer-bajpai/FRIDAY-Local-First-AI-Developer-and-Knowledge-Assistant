"""Build a small local dark report from the agent's tool usage log."""
from __future__ import annotations

import collections
import html
import json
from pathlib import Path

from .config import HOME


def build_report(root: Path) -> Path:
    log = root / ".friday" / "usage.jsonl"
    rows = []
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    counts = collections.Counter(row.get("tool", "unknown") for row in rows)
    failures = collections.Counter(row.get("tool", "unknown") for row in rows if not row.get("ok", True))
    body = "".join(
        f"<tr><td>{html.escape(name)}</td><td>{count}</td><td>{failures[name]}</td></tr>"
        for name, count in counts.most_common()
    )
    output = HOME / "insights.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\">"
        "<title>Friday insights</title><style>body{background:#101214;color:#bbc2cb;font:15px ui-monospace,monospace;padding:2rem}"
        "h1{color:#8b9aa8}table{border-collapse:collapse}td,th{padding:.55rem 1rem;border-bottom:1px solid #343a40;text-align:left}</style>"
        f"<h1>Friday insights</h1><p>{len(rows)} recorded tool calls</p>"
        f"<table><thead><tr><th>Tool</th><th>Calls</th><th>Errors</th></tr></thead><tbody>{body}</tbody></table></html>",
        encoding="utf-8",
    )
    return output