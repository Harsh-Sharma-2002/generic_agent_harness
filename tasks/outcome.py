"""Outcome of a KernelAI task execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tasks.task import TaskStatus


@dataclass(frozen=True)
class TaskOutcome:
    """
    Result returned after executing one KernelAI task.
    """

    request_id: str
    task_id: str
    status: TaskStatus

    result: dict[str, Any] | None = None
    error: str | None = None
