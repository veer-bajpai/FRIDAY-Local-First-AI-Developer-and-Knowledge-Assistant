"""Development diagnostics for local inference: one JSON line per agent turn.

File: <FRIDAY_DATA_DIR>/diagnostics/agent_turns.jsonl (rotated at 5 MB). Each line records the
model, whether tools were sent, time to first token, prompt and generation token counts and
durations (from Ollama's own counters), tokens per second, model load time, tool time, retries,
the final status and the backend's memory use. Nothing leaves the machine; prompt text is not logged.

Read it with:  python -m friday_agent.diagnostics  [path]
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("friday.perf")
MAX_BYTES = 5_000_000


def log_path() -> Path:
    return Path(os.getenv("FRIDAY_DATA_DIR", "./data")) / "diagnostics" / "agent_turns.jsonl"


def process_rss_mb() -> float | None:
    """Resident memory of this process in MB (Linux /proc, psutil if installed, else None)."""
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    try:
        import psutil  # optional

        return round(psutil.Process().memory_info().rss / 1_048_576, 1)
    except Exception:  # noqa: BLE001 - optional dependency, any failure means "unknown"
        return None


def record_turn(session_id: str, metrics: dict[str, Any]) -> None:
    entry = {"ts": round(time.time(), 3), "session": session_id[:8], **metrics,
             "backend_rss_mb": process_rss_mb()}
    log.info("turn %s status=%s ttft=%ss total=%ss prompt=%s tok gen=%s tok %s tok/s tools=%s retries=%s",
             entry["session"], entry.get("status"), entry.get("ttft_s"), entry.get("total_s"),
             entry.get("prompt_tokens"), entry.get("gen_tokens"), entry.get("tokens_per_s"),
             entry.get("tools"), entry.get("retries"))
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_BYTES:
            os.replace(path, path.with_suffix(".jsonl.1"))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass  # diagnostics must never break a turn


def summarize(path: Path) -> str:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not rows:
        return "no turns recorded"

    def avg(key: str, subset: list[dict[str, Any]]) -> str:
        values = [r[key] for r in subset if isinstance(r.get(key), (int, float))]
        return f"{sum(values) / len(values):.2f}" if values else "-"

    out = [f"{len(rows)} turns  ({path})"]
    def used_tools(row: dict[str, Any]) -> bool:
        return row.get("tools") not in (None, False, "none")

    for label, subset in (("all", rows), ("with tools", [r for r in rows if used_tools(r)]),
                          ("chat only", [r for r in rows if not used_tools(r)])):
        if subset:
            out.append(f"  {label:<11} n={len(subset):<4} ttft {avg('ttft_s', subset)}s  total {avg('total_s', subset)}s  "
                       f"prompt {avg('prompt_tokens', subset)} tok  gen {avg('tokens_per_s', subset)} tok/s")
    statuses: dict[str, int] = {}
    for r in rows:
        statuses[str(r.get("status"))] = statuses.get(str(r.get("status")), 0) + 1
    out.append("  status " + ", ".join(f"{k} {v}" for k, v in sorted(statuses.items())))
    out.append(f"  retries {sum(int(r.get('retries') or 0) for r in rows)} · "
               f"recovered text tool calls {sum(int(r.get('text_tool_calls') or 0) for r in rows)} · "
               f"escalations {sum(1 for r in rows if r.get('escalated'))}")
    return "\n".join(out)


if __name__ == "__main__":
    print(summarize(Path(sys.argv[1]) if len(sys.argv) > 1 else log_path()))
