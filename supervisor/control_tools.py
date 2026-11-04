"""Privileged control tools available to a KernelAI Supervisor."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from second_brain.models import SecondBrainError
from supervisor.escalation import apply_decision
from supervisor.models import (
    EscalationDecision,
    EscalationDecisionKind,
)
from tasks.models import TaskState
from tasks.task import Task, TaskStatus


if TYPE_CHECKING:
    from supervisor.supervisor import Supervisor


DELEGATE_TASK_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "delegate_task",
        "description": (
            "Create one independently executable KernelAI task and "
            "authorize the domains it may use. You choose the Task "
            "and its authorized domains; you do not choose "
            "individual skills or methodologies. The Task Worker "
            "selects methodologies within the authorized domains. "
            "Choose the smallest sufficient set of domains."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Clear, self-contained instruction describing "
                        "the work the Task Worker must complete."
                    ),
                },
                "domains": {
                    "type": "array",
                    "description": (
                        "Domains the Task Worker is authorized to use "
                        "for this Task. Grant only the smallest "
                        "sufficient set."
                    ),
                    "items": {
                        "type": "string",
                    },
                    "minItems": 1,
                },
            },
            "required": [
                "task",
                "domains",
            ],
            "additionalProperties": False,
        },
    },
}


RESOLVE_ESCALATION_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "resolve_escalation",
        "description": (
            "Resolve one BLOCKED Task's stored escalation. The Task "
            "Worker already recorded what authority it needs and why; "
            "do not restate those fields. Choose 'approve' when that "
            "expansion is necessary, 'deny' to retry the same Task "
            "under its existing authority, or 'terminate' when the "
            "Task cannot usefully continue. Approving grants only the "
            "minimum authority represented by the stored escalation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": (
                        "Identifier of the BLOCKED Task to resolve."
                    ),
                },
                "decision": {
                    "type": "string",
                    "enum": [
                        "approve",
                        "deny",
                        "terminate",
                    ],
                    "description": (
                        "approve expands authority, deny retries "
                        "under existing authority, terminate ends "
                        "the Task."
                    ),
                },
                "suggestion": {
                    "type": "string",
                    "description": (
                        "Optional advisory guidance for the next "
                        "attempt, at most 150 characters. It cannot "
                        "grant authority or change accumulated work."
                    ),
                },
            },
            "required": [
                "task_id",
                "decision",
            ],
            "additionalProperties": False,
        },
    },
}


class SupervisorControlTools:
    """
    Privileged model-facing operations available to one Supervisor.

    These tools may create resumable Tasks for the Supervisor's
    Request, but they never execute those Tasks.

    Task execution remains the responsibility of the Supervisor
    runtime.
    """

    def __init__(
        self,
        supervisor: Supervisor,
    ) -> None:
        self.supervisor = supervisor

    async def delegate_task(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, str]:
        """
        Validate and register one Task for later execution.

        The Task is added to the Supervisor's current pending batch.
        Execution does not happen inside this control tool.
        """

        task_query = arguments.get(
            "task"
        )

        domains = arguments.get(
            "domains"
        )

        # Validate task instruction.

        if not isinstance(
            task_query,
            str,
        ):
            raise ValueError(
                "delegate_task requires 'task' to be a string."
            )

        task_query = task_query.strip()

        if not task_query:
            raise ValueError(
                "delegate_task requires a non-empty 'task'."
            )

        # Validate domain list.

        if not isinstance(
            domains,
            list,
        ) or not domains:
            raise ValueError(
                "delegate_task requires a non-empty 'domains' list."
            )

        normalized_domains: list[str] = []

        for domain in domains:
            if not isinstance(
                domain,
                str,
            ):
                raise ValueError(
                    "Every domain must be a string."
                )

            domain = domain.strip()

            if not domain:
                raise ValueError(
                    "Domain names cannot be empty."
                )

            # Deterministically verify that the requested domain
            # currently exists in the Second Brain vault.
            #
            # The DOMAIN.md itself is not returned or injected here.
            result = self.supervisor.second_brain.get_domain(
                domain
            )

            if isinstance(
                result,
                SecondBrainError,
            ):
                raise ValueError(
                    f"Unknown domain {domain!r}: {result.message}"
                )

            if domain not in normalized_domains:
                normalized_domains.append(
                    domain
                )

        # Create Task and its persistent execution state.

        task_id = (
            f"task_{uuid.uuid4().hex}"
        )

        task = Task(
            request_id=self.supervisor.request.request_id,
            task_id=task_id,
            query=task_query,
            authorized_domains=normalized_domains,
        )

        task_state = TaskState(
            task_id=task_id,
            instruction=task_query,
            authorized_domains=set(normalized_domains),
        )

        # Lifetime registries for this Request.

        self.supervisor.tasks[
            task_id
        ] = task

        self.supervisor.task_states[
            task_id
        ] = task_state

        # Current execution batch.
        self.supervisor.pending_tasks.append(
            task
        )

        # Task creation and registration succeeded.
        self.supervisor._emit(
            event="task_delegated",
            task_id=task.task_id,
            attempt=task_state.attempt,
            domains=task.authorized_domains,
            task=task_query,
        )

        # The model only needs the identifier.
        return {
            "task_id": task_id,
        }

    async def resolve_escalation(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Apply the Supervisor's decision to one BLOCKED Task.

        The requested authority is read from the canonical
        TaskState.escalation, so the model cannot restate or invent
        it. Only the decision and attempt-local guidance come from
        the control action.
        """

        task_id = arguments.get(
            "task_id"
        )

        if not isinstance(
            task_id,
            str,
        ) or not task_id.strip():
            raise ValueError(
                "resolve_escalation requires a non-empty 'task_id'."
            )

        task_id = task_id.strip()

        task = self.supervisor.tasks.get(task_id)
        state = self.supervisor.task_states.get(task_id)

        if task is None or state is None:
            raise ValueError(
                f"Unknown task_id {task_id!r}."
            )

        if task.status is not TaskStatus.BLOCKED:
            raise ValueError(
                f"Task {task_id!r} is not BLOCKED "
                f"(status {task.status.value!r})."
            )

        if state.escalation is None:
            raise ValueError(
                f"Task {task_id!r} has no active escalation."
            )

        resolved = self.supervisor.resolved_escalations.get(
            task_id
        )

        if resolved is None:
            raise ValueError(
                f"Task {task_id!r} has no validated escalation "
                "awaiting a decision."
            )

        raw_decision = arguments.get(
            "decision"
        )

        if not isinstance(
            raw_decision,
            str,
        ):
            raise ValueError(
                "resolve_escalation requires a 'decision' of "
                "approve, deny, or terminate."
            )

        try:
            kind = EscalationDecisionKind(
                raw_decision.strip().lower()
            )

        except ValueError as exc:
            raise ValueError(
                "resolve_escalation requires a 'decision' of "
                "approve, deny, or terminate."
            ) from exc

        suggestion = arguments.get(
            "suggestion"
        )

        if suggestion is not None:
            if not isinstance(
                suggestion,
                str,
            ):
                raise ValueError(
                    "'suggestion' must be a string when provided."
                )

            suggestion = suggestion.strip() or None

        # Rejects an over-long suggestion instead of truncating it.
        decision = EscalationDecision(
            decision=kind,
            suggestion=suggestion,
        )

        resolution = apply_decision(
            task=task,
            state=state,
            decision=decision,
            resolved=resolved,
            select_domain=(
                self.supervisor._select_tool_domain
            ),
        )

        self.supervisor.resolved_escalations.pop(
            task_id,
            None,
        )

        if resolution.restart:
            # Same Task, same TaskState: the retry runs in the batch
            # produced by this Supervisor step, alongside any newly
            # delegated Tasks.
            self.supervisor.pending_tasks.append(
                task
            )

            if decision.suggestion is not None:
                self.supervisor.restart_suggestions[
                    task_id
                ] = decision.suggestion

        else:
            self.supervisor.restart_suggestions.pop(
                task_id,
                None,
            )

        self.supervisor._emit(
            event="escalation_resolved",
            task_id=task_id,
            kind=resolution.kind.value,
            decision=decision.decision.value,
            suggestion=decision.suggestion,
            candidate_domains=list(
                resolved.candidate_domains
            ),
            granted_domain=resolution.granted_domain,
            domains_before=list(
                resolution.domains_before
            ),
            domains_after=list(
                resolution.domains_after
            ),
            skill_limit_before=(
                resolution.skill_limit_before
            ),
            skill_limit_after=(
                resolution.skill_limit_after
            ),
            attempt_before=resolution.attempt_before,
            attempt_after=resolution.attempt_after,
            restart=resolution.restart,
        )

        return {
            "task_id": task_id,
            "status": task.status.value,
            "attempt": state.attempt,
        }

    def get_tools(
        self,
    ) -> dict[
        str,
        tuple[
            dict[str, Any],
            Any,
        ],
    ]:
        """
        Return control tools in ControlToolClient registration format.
        """

        return {
            "delegate_task": (
                DELEGATE_TASK_SCHEMA,
                self.delegate_task,
            ),
            "resolve_escalation": (
                RESOLVE_ESCALATION_SCHEMA,
                self.resolve_escalation,
            ),
        }
