"""Default FRIDAY tool hooks.

The current project configuration has no custom hooks.
This module provides the interface expected by friday_agent.engine.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any


class Hooks:
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

        if config:
            raise ValueError(
                "Custom hooks are not implemented in this FRIDAY version. "
                "Remove the unsupported hooks configuration rather than "
                "silently ignoring it."
            )

    def run(
        self,
        event: str,
        tool: str,
        arguments: dict[str, Any],
        result: Any = None,
    ) -> tuple[bool, str] | None:
        """Handle the hook events currently called by the agent engine."""

        if event == "PreToolUse":
            # No custom pre-tool hooks are configured.
            # The separate Permissions system still controls tool access.
            return True, ""

        if event == "PostToolUse":
            # No custom post-tool hooks are configured.
            return None

        raise ValueError(f"Unsupported FRIDAY hook event: {event}")
