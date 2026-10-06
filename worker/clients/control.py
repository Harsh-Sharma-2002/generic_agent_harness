"""Privileged local tool client used by KernelAI supervisors."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable


ControlToolHandler = Callable[
    [dict[str, Any]],
    Awaitable[Any],
]

ControlTool = tuple[
    dict[str, Any],
    ControlToolHandler,
]


class ControlToolClient:
    """
    Tool client for privileged KernelAI control operations.

    Only explicitly registered local control tools are exposed.

    This client does not provide access to MCP tools.
    """

    def __init__(
        self,
        tools: dict[str, ControlTool],
    ) -> None:
        self.tools = tools

    async def get_tools(
        self,
    ) -> list[dict[str, Any]]:
        """
        Return the schemas of all registered control tools.
        """

        return [
            schema
            for schema, _ in self.tools.values()
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        """
        Execute a registered privileged control tool.
        """

        try:
            _, handler = self.tools[name]

        except KeyError as exc:
            raise ValueError(
                f"Unknown control tool {name!r}."
            ) from exc

        result = await handler(
            arguments
        )

        if isinstance(
            result,
            str,
        ):
            return result

        return json.dumps(
            result,
            default=str,
        )