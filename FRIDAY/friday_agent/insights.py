"""Generate a local HTML activity report for Friday agent sessions."""
from __future__ import annotations

import html
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _usage_entries(root: Path) -> list[dict[str, Any]]:
    log = root / ".friday" / "usage.jsonl"
    entries: list[dict[str, Any]] = []
    if not log.is_file():
        return entries
    try:
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[-5000:]:
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    entries.append(item)
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return entries


def build_report(root: str | Path) -> Path:
    workspace = Path(root).expanduser().resolve()
    output_dir = workspace / ".friday"
    output_dir.mkdir(parents=True, exist_ok=True)
    report = output_dir / "insights.html"
    entries = _usage_entries(workspace)
    counts = Counter(str(item.get("tool", "unknown")) for item in entries)
    successes = sum(1 for item in entries if item.get("ok"))
    total = len(entries)

    rows = "".join(
        f"<tr><td>{html.escape(tool)}</td><td>{count}</td></tr>"
        for tool, count in counts.most_common()
    ) or "<tr><td colspan=\"2\">No tool activity recorded.</td></tr>"
    document = f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>FRIDAY Insights</title>
<style>body{{font-family:system-ui,sans-serif;max-width:900px;margin:3rem auto;padding:0 1.5rem;color:#20232a}}table{{border-collapse:collapse;width:100%}}th,td{{border-bottom:1px solid #ddd;padding:.6rem;text-align:left}}.stats{{display:flex;gap:2rem}}.stats div{{font-size:1.2rem}}</style>
</head>
<body>
<h1>FRIDAY Agent Insights</h1>
<p>Workspace: <code>{html.escape(str(workspace))}</code></p>
<div class="stats"><div><strong>{total}</strong><br>tool calls</div><div><strong>{successes}</strong><br>successful calls</div></div>
<h2>Tool usage</h2><table><thead><tr><th>Tool</th><th>Calls</th></tr></thead><tbody>{rows}</tbody></table>
</body>
</html>
"""
    report.write_text(document, encoding="utf-8")
    return report

