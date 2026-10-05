"""KernelAI task representation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    """
    Lifecycle state of a KernelAI task.
    """

    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    """
    A unit of work executed by KernelAI.

    request_id identifies the complete user request.

    task_id identifies this specific unit of work within
    that request.
    """

    request_id: str
    task_id: str
    query: str

    skill_name: str 
    parent_task_id: str 

    status: TaskStatus = TaskStatus.QUEUED

    result: dict[str, Any] | None = None
    error: str | None = None