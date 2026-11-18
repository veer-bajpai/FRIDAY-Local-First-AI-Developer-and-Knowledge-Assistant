"""Configurable, bounded hooks for agent tool execution."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from fnmatch import fnmatch
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, overload


class Hooks:
    SUPPORTED_EVENTS = {"PreToolUse", "PostToolUse"}

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        workspace: str | Path | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve() if workspace is not None else None
        if config is None:
            config = {}
        if not isinstance(config, Mapping):
            raise TypeError("Hooks configuration must be a mapping.")
        unknown = set(config) - self.SUPPORTED_EVENTS
        if unknown:
            raise ValueError(f"Unsupported FRIDAY hook event(s): {', '.join(sorted(unknown))}")
        self.config: dict[str, list[dict[str, Any]]] = {}
        for event in self.SUPPORTED_EVENTS:
            entries = config.get(event, [])
            if entries is None:
                entries = []
            if isinstance(entries, Mapping):
                entries = [entries]
            if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
                raise TypeError(f"{event} hooks must be a mapping or a list.")
            normalized = []
            for entry in entries:
                if not isinstance(entry, Mapping):
                    raise TypeError(f"{event} hook entries must be mappings.")
                normalized.append(dict(entry))
            self.config[event] = normalized

    def _matches(self, entry: Mapping[str, Any], tool: str) -> bool:
        matcher = entry.get("tool")
        if matcher is None:
            # Older FRIDAY settings use a glob "matcher" (e.g. "bash" or "git_*").
            pattern = entry.get("matcher", "*")
            if not isinstance(pattern, str):
                raise TypeError("Hook 'matcher' must be a string glob.")
            return fnmatch(tool, pattern)
        if isinstance(matcher, str):
            return matcher == tool
        if isinstance(matcher, Sequence) and not isinstance(matcher, (str, bytes)):
            return tool in matcher
        raise TypeError("Hook 'tool' must be a string or list of strings.")

    @overload
    def run(
        self,
        event: Literal["PreToolUse"],
        tool: str,
        arguments: dict[str, Any],
        result: Any = None,
    ) -> tuple[bool, str]: ...

    @overload
    def run(
        self,
        event: Literal["PostToolUse"],
        tool: str,
        arguments: dict[str, Any],
        result: Any = None,
    ) -> None: ...

    def run(
        self,
        event: str,
        tool: str,
        arguments: dict[str, Any],
        result: Any = None,
    ) -> tuple[bool, str] | None:
        if event not in self.SUPPORTED_EVENTS:
            raise ValueError(f"Unsupported FRIDAY hook event: {event}")
        for entry in self.config[event]:
            if not self._matches(entry, tool):
                continue
            command = entry.get("command")
            if not isinstance(command, str) or not command.strip():
                raise ValueError(f"{event} hook for '{tool}' requires a non-empty command.")
            argv: str | list[str]
            if os.name == "nt":
                # Windows parses the command line itself (CreateProcess); still no shell.
                argv = command.strip()
            else:
                try:
                    argv = shlex.split(command)
                except ValueError as exc:
                    return (False, f"Invalid hook command: {exc}") if event == "PreToolUse" else None
                if not argv:
                    return (False, "Invalid empty hook command.") if event == "PreToolUse" else None
            try:
                timeout = min(max(float(entry.get("timeout", 30)), 1.0), 300.0)
            except (TypeError, ValueError):
                timeout = 30.0
            # "args"/"output" kept alongside the new keys so older hook scripts keep working.
            payload = json.dumps({"event": event, "tool": tool, "arguments": arguments, "result": result,
                                  "args": arguments, "output": result}, default=str)
            env = os.environ.copy()
            env["FRIDAY_HOOK_EVENT"] = event
            env["FRIDAY_HOOK_TOOL"] = tool
            try:
                completed = subprocess.run(
                    argv,
                    cwd=self.workspace,
                    input=payload,
                    text=True,
                    capture_output=True,
                    timeout=timeout,
                    env=env,
                    shell=False,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                if event == "PreToolUse":
                    return False, "PreToolUse hook timed out."
                continue
            except OSError as exc:
                if event == "PreToolUse":
                    return False, f"PreToolUse hook could not run: {exc}"
                continue
            if event == "PreToolUse" and completed.returncode != 0:
                reason = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
                return False, reason
        return (True, "") if event == "PreToolUse" else None
