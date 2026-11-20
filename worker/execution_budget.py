"""Attempt-scoped deterministic execution budgets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolBudgetReservation:
    """Result of attempting to reserve one tool invocation."""

    allowed: bool
    tool_name: str
    limit: int | None
    used: int
    remaining: int | None


class ExecutionBudget:
    """
    Attempt-scoped execution resource budget.

    Skills may declare limits for execution tools. Loading a skill
    registers those limits here. The execution-tool boundary reserves
    budget before invoking the underlying tool.

    This object does not grant tools or domains. It only limits tools
    that the Worker already has authority to call.
    """

    def __init__(self) -> None:
        self._tool_limits: dict[str, int] = {}
        self._tool_used: dict[str, int] = {}

    def register_tool_limits(
        self,
        limits: dict[str, int],
    ) -> None:
        """
        Register methodology-declared tool limits.

        If multiple loaded methodologies limit the same tool, the
        strictest limit wins.

        Registering a limit never resets already-consumed budget.
        """

        for tool_name, limit in limits.items():
            current_limit = self._tool_limits.get(tool_name)

            if current_limit is None:
                self._tool_limits[tool_name] = limit
            else:
                self._tool_limits[tool_name] = min(
                    current_limit,
                    limit,
                )

    def reserve_tool(
        self,
        tool_name: str,
    ) -> ToolBudgetReservation:
        """
        Reserve one invocation of an execution tool.

        Unlimited tools are allowed without accounting.

        Limited tools consume budget when the reservation succeeds,
        before the underlying tool executes. A failed underlying tool
        call therefore still consumes its reservation.
        """

        limit = self._tool_limits.get(tool_name)
        used = self._tool_used.get(tool_name, 0)

        if limit is None:
            return ToolBudgetReservation(
                allowed=True,
                tool_name=tool_name,
                limit=None,
                used=used,
                remaining=None,
            )

        if used >= limit:
            return ToolBudgetReservation(
                allowed=False,
                tool_name=tool_name,
                limit=limit,
                used=used,
                remaining=0,
            )

        used += 1
        self._tool_used[tool_name] = used

        return ToolBudgetReservation(
            allowed=True,
            tool_name=tool_name,
            limit=limit,
            used=used,
            remaining=limit - used,
        )

    def limit_for(
        self,
        tool_name: str,
    ) -> int | None:
        """Return the effective limit for a tool, if one exists."""

        return self._tool_limits.get(tool_name)

    def used_for(
        self,
        tool_name: str,
    ) -> int:
        """Return the number of consumed calls for a tool."""

        return self._tool_used.get(tool_name, 0)

    def remaining_for(
        self,
        tool_name: str,
    ) -> int | None:
        """Return remaining calls, or None when the tool is unlimited."""

        limit = self._tool_limits.get(tool_name)

        if limit is None:
            return None

        used = self._tool_used.get(tool_name, 0)

        return max(limit - used, 0)