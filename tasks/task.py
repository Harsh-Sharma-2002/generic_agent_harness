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
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    """
    A unit of work executed by KernelAI.

    request_id identifies the complete user request.

    task_id identifies this specific unit of work within
    that request.

    A Task is the semantic definition of the work and its lifecycle.
    It does not carry execution state: progress, loaded skills,
    attempts, and escalation belong to TaskState, which persists
    across execution attempts.
    """

    request_id: str
    task_id: str
    query: str

    authorized_domains: list[str]

    status: TaskStatus = TaskStatus.QUEUED
