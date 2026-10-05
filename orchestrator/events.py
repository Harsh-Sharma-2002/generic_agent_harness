"""Task lifecycle events sent back to the KernelAI orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TaskCompleted:
    """
    A worker successfully completed a delegated task.
    """

    request_id: str
    task_id: str
    result: dict[str, Any]


@dataclass(frozen=True)
class TaskFailed:
    """
    A worker failed while executing a delegated task.
    """

    request_id: str
    task_id: str
    error: str


TaskEvent = TaskCompleted | TaskFailed

