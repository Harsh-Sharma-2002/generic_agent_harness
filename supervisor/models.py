"""Supervisor-owned KernelAI models.

Shared Task-layer models live in ``tasks.models`` and
``tasks.task``. This module holds only the decision a Supervisor
makes about a Worker's escalation request.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


MAX_SUGGESTION_LENGTH = 150


class EscalationDecisionKind(str, Enum):
    """
    Supervisor's answer to one validated escalation.
    """

    APPROVE = "approve"
    DENY = "deny"
    TERMINATE = "terminate"


@dataclass(frozen=True, slots=True)
class EscalationDecision:
    """
    The Supervisor's decision on one escalation.

    APPROVE expands the Task's authority as the stored escalation
    requested.

    DENY keeps authority unchanged and retries the Task under its
    existing boundary.

    TERMINATE ends the Task: no replacement Worker is started.

    ``suggestion`` is advisory guidance for the next attempt only. It
    can never change authority or TaskState, so it is bounded and
    rejected rather than truncated when it is too long.
    """

    decision: EscalationDecisionKind
    suggestion: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(
            self.decision,
            EscalationDecisionKind,
        ):
            object.__setattr__(
                self,
                "decision",
                EscalationDecisionKind(
                    self.decision
                ),
            )

        suggestion = self.suggestion

        if suggestion is not None:
            suggestion = suggestion.strip() or None

        if (
            suggestion is not None
            and len(suggestion) > MAX_SUGGESTION_LENGTH
        ):
            raise ValueError(
                "Escalation suggestion must be at most "
                f"{MAX_SUGGESTION_LENGTH} characters."
            )

        object.__setattr__(
            self,
            "suggestion",
            suggestion,
        )
