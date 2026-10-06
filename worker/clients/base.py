"""Tool client contract used by KernelAI agents."""

from __future__ import annotations

from typing import Any, Protocol


class ToolClient(Protocol):
    """
    Tool interface required by GenericWorker.

    The concrete client determines which capabilities
    an agent is allowed to access.
    """

    async def get_tools(
        self,
    ) -> list[dict[str, Any]]:
        """Return available OpenAI-compatible tool definitions."""
        ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        """Execute an available tool and return its result as text."""
        ...