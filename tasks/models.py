"""Shared KernelAI task models.

Holds the structured types that cross the Task boundary but are
neither the semantic Task definition (tasks.task) nor the result of
one execution attempt (tasks.outcome):

    EscalationRequest
    = a Worker's structured request for authority expansion

    TaskState
    = persistent resumable execution state for one semantic Task
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EscalationKind(str, Enum):
    """
    Kind of Supervisor authority a Worker is asking for.
    """

    DOMAIN_REQUIRED = "domain_required"
    TOOL_REQUIRED = "tool_required"
    SKILL_LIMIT_EXCEEDED = "skill_limit_exceeded"


@dataclass(frozen=True, slots=True)
class EscalationRequest:
    """
    A Worker's request to expand the authority of its current Task.

    The request describes what is needed and why. It carries no
    approval state: whether authority is granted is a Supervisor
    decision, and it is recorded on the Task's persistent state, not
    here.
    """

    kind: EscalationKind
    reason: str

    requested_domain: str | None = None
    requested_tool: str | None = None
    requested_skill_limit: int | None = None


@dataclass(slots=True)
class TaskState:
    """
    Persistent resumable execution state for one semantic Task.

    A Task definition says what work must be done. TaskState says how
    far a Worker has got in doing it, so that execution can survive
    interruption and resume rather than restart.

    The same Task may be executed more than once. Each attempt
    continues from the accumulated state here and returns its own
    immutable TaskOutcome.
    """

    task_id: str
    instruction: str

    attempt: int = 1
    authorized_domains: set[str] = field(default_factory=set)
    skill_limit: int = 5

    loaded_skills: list[str] = field(default_factory=list)

    progress: str = ""
    findings: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    remaining_work: str = ""

    escalation: EscalationRequest | None = None
