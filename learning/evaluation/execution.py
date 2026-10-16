"""Execution models used by KernelAI offline evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCallRecord:
    """
    One tool invocation made during an evaluated execution.
    """

    tool_call_id: str
    tool_name: str

    arguments: Any
    result: Any | None

    success: bool
    duration_seconds: float | None

    iteration: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class TaskExecutionRecord:
    """
    Execution record for one KernelAI Task.
    """

    task_id: str
    description: str

    skills: tuple[str, ...]

    status: str

    llm_calls: int
    tool_calls: tuple[ToolCallRecord, ...]

    duration_seconds: float | None


@dataclass(frozen=True)
class ExecutionRecord:
    """
    Complete execution record for one KernelAI Request.

    This is the stable boundary between runtime observability and
    offline evaluation.
    """

    request_id: str
    query: str

    status: str
    final_answer: Any | None

    duration_seconds: float | None

    tasks: tuple[TaskExecutionRecord, ...]

    supervisor_llm_calls: int

    input_tokens: int | None = None
    output_tokens: int | None = None

    metadata: dict[str, Any] = field(
        default_factory=dict,
    )