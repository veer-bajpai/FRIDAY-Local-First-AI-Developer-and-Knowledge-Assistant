"""Coding-agent tools adapted from FRIDAY's path-confined local tool library."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from friday_agent.git_ops import _run_git
from friday_agent.tools import Context, ToolError, _truncate, build_tools

MAX_COMMAND_TIMEOUT = 300

DENY_PATTERNS = (
    re.compile(r"\b(?:rm|rmdir)\s+-[a-z]*r[a-z]*f[a-z]*\b", re.IGNORECASE),
    re.compile(r"\b(?:del|erase)\b[^\r\n]*/s\b[^\r\n]*/q\b", re.IGNORECASE),
    re.compile(r"\bRemove-Item\b(?=[^\r\n]*\-(?:Recurse|r)\b)(?=[^\r\n]*\-(?:Force|f)\b)", re.IGNORECASE),
    re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE),
    re.compile(r"\b(?:shutdown|mkfs|diskpart|Clear-Disk)\b", re.IGNORECASE),
    re.compile(r":\(\)\s*\{\s*:\|:\s*&\s*\};?:"),
    re.compile(r"\b(?:curl|wget)[^|\r\n]*\|\s*(?:sh|bash|zsh|powershell)\b", re.IGNORECASE),
    re.compile(r"\bgit\s+push\b[^\r\n]*--force(?:-with-lease)?\b", re.IGNORECASE),
    re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE),
    re.compile(r"\bgit\s+clean\s+-[a-z]*[fd][a-z]*\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    parameters: dict[str, Any]
    level: str
    run: Callable[[dict[str, Any], Context], Awaitable[str]]
    preview: Callable[[dict[str, Any], Context], str] | None = None

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _wrapped_tool(name: str, level: str) -> AgentTool:
    friday_tool = build_tools()[name]
    schema = friday_tool.schema()["function"]
    return AgentTool(
        name=name,
        description=schema["description"],
        parameters=schema["parameters"],
        level=level,
        run=friday_tool.run,
        preview=friday_tool.preview,
    )


async def _run_command(args: dict[str, Any], context: Context) -> str:
    command = str(args.get("command", "")).strip()
    if not command:
        raise ToolError("Command must not be empty.")
    if any(pattern.search(command) for pattern in DENY_PATTERNS):
        raise ToolError("Command blocked by the coding-agent safety denylist.")
    try:
        timeout = int(args.get("timeout", 60))
    except (TypeError, ValueError) as exc:
        raise ToolError("Command timeout must be an integer.") from exc
    if not 1 <= timeout <= MAX_COMMAND_TIMEOUT:
        raise ToolError(f"Command timeout must be between 1 and {MAX_COMMAND_TIMEOUT} seconds.")
    return await build_tools()["bash"].run(
        {"command": command, "timeout": timeout}, context
    )


async def _git_status(_args: dict[str, Any], context: Context) -> str:
    return _truncate(await _run_git(context, ["status", "--short", "--branch"]))


async def _git_diff(_args: dict[str, Any], context: Context) -> str:
    return _truncate(await _run_git(context, ["diff", "HEAD"]))


async def _git_commit(args: dict[str, Any], context: Context) -> str:
    message = str(args.get("message", "")).strip()
    if not message:
        raise ToolError("Commit message must not be empty.")
    staged = await _run_git(context, ["add", "-A"])
    committed = await _run_git(context, ["commit", "-m", message])
    return f"{staged}\n{committed}"


def _command_preview(args: dict[str, Any], _context: Context) -> str:
    return f"$ {args.get('command', '')}"


def _commit_preview(args: dict[str, Any], _context: Context) -> str:
    return f'git add -A && git commit -m "{args.get("message", "")}"'


def get_tools() -> dict[str, AgentTool]:
    """Return only the coding-agent tool allowlist, not every Friday integration."""
    return {
        "list_dir": _wrapped_tool("list_dir", "read"),
        "glob": _wrapped_tool("glob", "read"),
        "grep": _wrapped_tool("grep", "read"),
        "read_file": _wrapped_tool("read_file", "read"),
        "edit_file": _wrapped_tool("edit_file", "write"),
        "write_file": _wrapped_tool("write_file", "write"),
        "todo_write": _wrapped_tool("todo_write", "read"),
        "run_command": AgentTool(
            name="run_command",
            description="Run a non-interactive command from the project root. Output is truncated and execution has a timeout.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": MAX_COMMAND_TIMEOUT},
                },
                "required": ["command"],
            },
            level="exec",
            run=_run_command,
            preview=_command_preview,
        ),
        "git_status": AgentTool(
            "git_status", "Show the current Git working-tree status.",
            {"type": "object", "properties": {}, "required": []}, "read", _git_status,
        ),
        "git_diff": AgentTool(
            "git_diff", "Show the current uncommitted Git diff against HEAD.",
            {"type": "object", "properties": {}, "required": []}, "read", _git_diff,
        ),
        "git_commit": AgentTool(
            "git_commit", "Stage project changes and create a commit with this message.",
            {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
            "exec", _git_commit, _commit_preview,
        ),
    }


def tool_specs(tools: dict[str, AgentTool]) -> list[dict[str, Any]]:
    return [tool.spec() for tool in tools.values()]


def make_preview(tool: AgentTool, args: dict[str, Any], context: Context) -> str:
    if tool.preview is None:
        return ""
    return _truncate(tool.preview(args, context), 12_000)

