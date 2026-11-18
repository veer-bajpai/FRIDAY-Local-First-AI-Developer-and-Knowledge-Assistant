"""Compatibility tool wrappers for the legacy approval-gated agent loop."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from friday_agent.tools import Context, build_tools


@dataclass
class AgentTool:
    name: str
    description: str
    parameters: dict[str, Any]
    level: str
    run: Callable[[dict[str, Any], Context], Awaitable[str]]
    preview: Callable[[dict[str, Any], Context], str] | None = None


def get_tools() -> dict[str, AgentTool]:
    legacy: dict[str, AgentTool] = {}
    for name, tool in build_tools().items():
        level = "read" if tool.read_only else "write"
        legacy[name] = AgentTool(
            name=name,
            description=tool.description,
            parameters=tool.parameters,
            level=level,
            run=tool.run,
            preview=tool.preview,
        )
    return legacy


def tool_specs(tools: dict[str, AgentTool]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools.values()
    ]


def make_preview(tool: AgentTool, arguments: dict[str, Any], context: Context) -> str:
    if tool.preview:
        return tool.preview(arguments, context)
    if tool.name == "bash":
        return str(arguments.get("command", ""))
    if tool.name == "git_commit":
        return str(arguments.get("message", ""))
    if tool.name == "git_add":
        return "\n".join(str(path) for path in arguments.get("paths", []))
    return ""
