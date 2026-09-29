"""Configured hooks around agent tool calls."""
from __future__ import annotations

import json
import subprocess
from fnmatch import fnmatch
from pathlib import Path


class Hooks:
    def __init__(self, config: dict | None = None, cwd: Path | None = None):
        self.config = config or {}
        self.cwd = cwd or Path.cwd()

    def run(self, event: str, tool: str, args: dict, output: str | None = None) -> tuple[bool, str]:
        for hook in self.config.get(event, []):
            if not fnmatch(tool, hook.get("matcher", "*")):
                continue
            payload = json.dumps({"event": event, "tool": tool, "args": args, "output": output})
            try:
                result = subprocess.run(
                    hook["command"], shell=True, input=payload, text=True,
                    capture_output=True, cwd=self.cwd,
                    timeout=min(max(int(hook.get("timeout", 60)), 1), 300),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                if event == "PreToolUse":
                    return False, f"Hook failed: {exc}"
                continue
            if event == "PreToolUse" and result.returncode != 0:
                return False, (result.stderr or result.stdout).strip() or "PreToolUse hook failed."
        return True, ""