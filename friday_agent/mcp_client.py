"""Optional MCP stdio clients exposed as permission-gated Friday tools."""
from __future__ import annotations

import json
import os
from contextlib import AsyncExitStack
from pathlib import Path

from .tools import Context, Tool, ToolError


class MCPManager:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.stack = AsyncExitStack()
        self.sessions = {}
        self.tools: dict[str, Tool] = {}

    async def start(self) -> None:
        if not self.config_path.exists():
            return
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read MCP configuration: {exc}") from exc
        servers = config.get("servers", {})
        if not servers:
            return
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise RuntimeError("MCP servers are configured; install the optional 'mcp' package.") from exc

        await self.stack.__aenter__()
        self.errors: dict[str, str] = {}
        for server_name, server in servers.items():
            try:
                await self._start_server(server_name, server, StdioServerParameters, stdio_client, ClientSession)
            except Exception as exc:  # one broken server must not take the others (or FRIDAY) down
                self.errors[server_name] = str(exc) or type(exc).__name__

    async def _start_server(self, server_name, server, StdioServerParameters, stdio_client, ClientSession):
        params = StdioServerParameters(
            command=server["command"],
            args=server.get("args", []),
            env={**os.environ, **server.get("env", {})},
        )
        read_stream, write_stream = await self.stack.enter_async_context(stdio_client(params))
        session = await self.stack.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()
        self.sessions[server_name] = session
        for definition in (await session.list_tools()).tools:
            tool_name = f"mcp__{server_name}__{definition.name}"

            async def invoke(args: dict, ctx: Context, *, _server=server_name, _tool=definition.name) -> str:
                del ctx
                result = await self.sessions[_server].call_tool(_tool, args)
                if getattr(result, "isError", False):
                    raise ToolError(f"MCP tool {_server}.{_tool} returned an error.")
                return "\n".join(
                    getattr(content, "text", str(content)) for content in result.content
                )

            self.tools[tool_name] = Tool(
                tool_name,
                definition.description or definition.name,
                definition.inputSchema,
                invoke,
                network=True,
            )

    async def close(self) -> None:
        await self.stack.aclose()

