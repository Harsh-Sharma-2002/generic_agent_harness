"""Outcome of a KernelAI task execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tasks.models import EscalationRequest
from tasks.task import TaskStatus


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    """
    Result returned after executing one KernelAI task.

    This is the single immutable envelope for one execution attempt,
    whatever the status:

    COMPLETED carries a result and no escalation.

    BLOCKED preserves the progress, findings, artifacts, and
    remaining work accumulated so far, together with the structured
    EscalationRequest that stopped the attempt. A blocked Task is
    resumable, not finished.

    FAILED carries an error.
    """

    request_id: str
    task_id: str
    status: TaskStatus

    result: dict[str, Any] | None = None
    error: str | None = None

    progress: str = ""
    findings: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    remaining_work: str = ""

    escalation: EscalationRequest | None = None
