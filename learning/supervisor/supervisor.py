"""Offline Learning Supervisor runtime for KernelAI."""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any, Callable

from learning.tools import (
    evaluate_trace_batch,
    get_current_policy,
    get_execution_details,
    get_metric_outliers,
    list_learning_policies,
    propose_policy_candidate,
)
from worker.factory import create_agent


LEARNING_SUPERVISOR_ROLE_PATH = (
    Path(__file__).with_name(
        "learning_supervisor.md"
    )
)


# ============================================================
# Tool schemas
# ============================================================


LEARNING_TOOL_SCHEMAS: list[
    dict[str, Any]
] = [
    {
        "type": "function",
        "function": {
            "name": "evaluate_trace_batch",
            "description": (
                "Evaluate historical KernelAI traces and return "
                "compact aggregate metric trends. Use this first "
                "when starting an offline learning analysis."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plane": {
                        "type": "string",
                        "enum": [
                            "production",
                            "learning",
                            "evaluation",
                        ],
                        "description": (
                            "Trace plane to evaluate. Normally use "
                            "'production' when learning from normal "
                            "KernelAI behavior."
                        ),
                    },
                    "policy": {
                        "type": "string",
                        "description": (
                            "Optional complete policy text used to "
                            "calculate policy-compliance metrics."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "description": (
                            "Optional maximum number of recent "
                            "executions to evaluate."
                        ),
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_metric_outliers",
            "description": (
                "Inspect a small number of already-scored executions "
                "with the highest or lowest values for one metric. "
                "Use this after batch evaluation when a metric needs "
                "investigation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "evaluation_id": {
                        "type": "string",
                        "description": (
                            "Evaluation identifier returned by "
                            "evaluate_trace_batch."
                        ),
                    },
                    "metric": {
                        "type": "string",
                        "description": (
                            "Metric whose extreme executions should "
                            "be inspected."
                        ),
                    },
                    "direction": {
                        "type": "string",
                        "enum": [
                            "lowest",
                            "highest",
                        ],
                        "description": (
                            "Whether to return the lowest or highest "
                            "values for the selected metric."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": (
                            "Maximum number of outliers to return. "
                            "Prefer 3 to 5."
                        ),
                    },
                },
                "required": [
                    "evaluation_id",
                    "metric",
                ],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_execution_details",
            "description": (
                "Inspect the complete stored trajectory for exactly "
                "one KernelAI request. Use only when aggregate "
                "metrics and outlier reasoning are insufficient."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {
                        "type": "string",
                        "description": (
                            "Request identifier to inspect."
                        ),
                    },
                    "plane": {
                        "type": "string",
                        "enum": [
                            "production",
                            "learning",
                            "evaluation",
                        ],
                        "description": (
                            "Trace plane containing the request."
                        ),
                    },
                },
                "required": [
                    "request_id",
                ],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_learning_policies",
            "description": (
                "List policies known to offline learning and show "
                "whether each policy is enabled, manual-only, or "
                "disabled for candidate creation."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_policy",
            "description": (
                "Read the complete current production policy and "
                "its learning mode. This operation is read-only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "policy_name": {
                        "type": "string",
                        "enum": [
                            "supervisor",
                            "task_worker",
                            "web_search",
                            "text2sql",
                        ],
                        "description": (
                            "Production policy to inspect."
                        ),
                    },
                },
                "required": [
                    "policy_name",
                ],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_policy_candidate",
            "description": (
                "Create an isolated candidate replacement for an "
                "enabled production policy. The current policy is "
                "snapshotted first. This never modifies or releases "
                "the production policy."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "policy_name": {
                        "type": "string",
                        "enum": [
                            "supervisor",
                            "task_worker",
                            "web_search",
                            "text2sql",
                        ],
                        "description": (
                            "Policy the candidate is intended "
                            "to improve."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "Complete replacement policy content. "
                            "Supply the full policy, not a patch."
                        ),
                    },
                },
                "required": [
                    "policy_name",
                    "content",
                ],
                "additionalProperties": False,
            },
        },
    },
]


# ============================================================
# Local tool client
# ============================================================


class LearningToolClient:
    """
    Local ToolClient implementation exposing only approved offline
    learning capabilities to the Learning Supervisor.

    No arbitrary Python functions, filesystem paths, production
    writes, or release operations are exposed.
    """

    def __init__(self) -> None:
        self._tools: dict[
            str,
            Callable[..., Any],
        ] = {
            "evaluate_trace_batch": (
                evaluate_trace_batch
            ),
            "get_metric_outliers": (
                get_metric_outliers
            ),
            "get_execution_details": (
                get_execution_details
            ),
            "list_learning_policies": (
                list_learning_policies
            ),
            "get_current_policy": (
                get_current_policy
            ),
            "propose_policy_candidate": (
                propose_policy_candidate
            ),
        }

    async def get_tools(
        self,
    ) -> list[dict[str, Any]]:
        """
        Return the exact tool definitions available to the Learning
        Supervisor.
        """

        return LEARNING_TOOL_SCHEMAS

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        """
        Execute one approved local learning tool and return its result
        as JSON text suitable for an LLM tool-result message.
        """

        function = self._tools.get(
            name
        )

        if function is None:
            raise ValueError(
                f"Unknown learning tool {name!r}."
            )

        try:

            result = function(
                **arguments
            )

            if inspect.isawaitable(
                result
            ):
                result = await result

        except asyncio.CancelledError:
            raise

        except Exception as exc:

            # A failed learning tool must surface as a tool
            # result the supervisor can react to, not as an
            # exception that terminates the whole worker.

            return json.dumps(
                {
                    "ok": False,
                    "error": (
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    ),
                },
                ensure_ascii=False,
                default=str,
            )

        return json.dumps(
            result,
            ensure_ascii=False,
            default=str,
        )


# ============================================================
# Learning Supervisor
# ============================================================


class LearningSupervisor:
    """
    Run one offline KernelAI learning cycle.

    The Learning Supervisor analyzes historical execution evidence
    and may create isolated policy candidates when justified.

    It cannot modify or release production policies.
    """

    def __init__(
        self,
        *,
        max_iterations: int = 15,
        verbose: bool = False,
        local: bool = False,
    ) -> None:
        if max_iterations < 1:
            raise ValueError(
                "max_iterations must be at least 1."
            )

        role_prompt = (
            LEARNING_SUPERVISOR_ROLE_PATH.read_text(
                encoding="utf-8"
            )
        )

        self.tool_client = (
            LearningToolClient()
        )

        self.agent = create_agent(
            tools=self.tool_client,
            system_prompt=role_prompt,
            allowed_tools={
                "evaluate_trace_batch",
                "get_metric_outliers",
                "get_execution_details",
                "list_learning_policies",
                "get_current_policy",
                "propose_policy_candidate",
            },
            max_iterations=max_iterations,
            verbose=verbose,
            local=local,
            worker_role="learning_supervisor",
        )

    async def run(
        self,
        instruction: str | None = None,
    ) -> dict[str, Any]:
        """
        Run one offline learning cycle.

        When no custom instruction is supplied, analyze production
        history and create a candidate only when the evidence justifies
        one.
        """

        query = (
            instruction.strip()
            if (
                instruction is not None
                and instruction.strip()
            )
            else (
                "Analyze the available production execution history "
                "and perform one offline learning cycle. Follow the "
                "learning workflow. Create a policy candidate only "
                "when the evidence justifies a conservative change."
            )
        )

        return await self.agent.run(
            query=query,
            request_id=(
                "learning_supervisor"
            ),
        )
