"""Execution-tool client with attempt-scoped budget enforcement."""

from __future__ import annotations

import json
from typing import Any

from observability.tracer import RuntimeTracer
from worker.clients.base import ToolClient
from worker.execution_budget import ExecutionBudget


class BudgetedToolClient:
    """
    Enforce attempt-scoped execution-tool budgets.

    This client does not grant tools or modify authority. It wraps an
    already-authorized execution ToolClient and deterministically limits
    calls according to policies registered in ExecutionBudget.

    Budget is reserved before the underlying tool executes. Therefore,
    a failed underlying tool invocation still consumes its reservation.
    """

    def __init__(
        self,
        *,
        tools: ToolClient,
        budget: ExecutionBudget,
        request_id: str,
        task_id: str,
        attempt: int,
        trace: RuntimeTracer | None = None,
    ) -> None:
        self.tools = tools
        self.budget = budget
        self.request_id = request_id
        self.task_id = task_id
        self.attempt = attempt
        self.trace = trace

    async def get_tools(
        self,
    ) -> list[dict[str, Any]]:
        """
        Return the underlying execution-tool schemas unchanged.

        Budgeting restricts execution. It does not change tool
        discovery or authority.
        """

        return await self.tools.get_tools()

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        """
        Reserve budget before invoking an execution tool.

        Unlimited tools pass through unchanged. A limited tool is
        rejected deterministically once its budget is exhausted.
        """

        reservation = self.budget.reserve_tool(name)

        if not reservation.allowed:
            self._emit(
                "tool_budget_exhausted",
                tool=name,
                used=reservation.used,
                limit=reservation.limit,
                remaining=reservation.remaining,
            )

            return json.dumps(
                {
                    "success": False,
                    "error": {
                        "code": "tool_budget_exhausted",
                        "tool": name,
                        "limit": reservation.limit,
                        "used": reservation.used,
                        "remaining": reservation.remaining,
                    },
                    "instruction": (
                        "The runtime tool budget is exhausted. "
                        f"Do not call '{name}' again during this "
                        "Task attempt. Complete the Task using the "
                        "evidence already available."
                    ),
                }
            )

        if reservation.limit is not None:
            self._emit(
                "tool_budget_consumed",
                tool=name,
                used=reservation.used,
                limit=reservation.limit,
                remaining=reservation.remaining,
            )

        return await self.tools.call_tool(
            name=name,
            arguments=arguments,
        )

    def _emit(
        self,
        event: str,
        **data: Any,
    ) -> None:
        """Emit one execution-budget lifecycle event."""

        if self.trace is None:
            return

        self.trace.emit(
            component="execution_budget",
            event=event,
            request_id=self.request_id,
            task_id=self.task_id,
            attempt=self.attempt,
            **data,
        )
