"""Request-level Supervisor runtime for KernelAI."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from observability.tracer import RuntimeTracer
from second_brain.models import SecondBrainError
from second_brain.runtime import SecondBrainRuntime
from supervisor.control_tools import SupervisorControlTools
from supervisor.escalation import (
    EscalationValidationError,
    ResolvedEscalation,
    ToolAuthorityResolver,
    apply_outcome,
    validate_escalation,
)
from tool_grants.registry import TrustedDomainGrants
from tasks.models import TaskState
from tasks.outcome import TaskOutcome
from tasks.request import Request
from tasks.task import Task, TaskStatus
from tasks.task_executor import TaskExecutor
from worker.clients.control import ControlToolClient
from worker.factory import create_agent


SUPERVISOR_ROLE_PATH = (
    Path(__file__).with_name("supervisor.md")
)

VAULT_PATH_ENV = "KERNELAI_VAULT_PATH"


def _resolve_vault_path() -> str:
    """
    Resolve the configured Second Brain vault location.

    The vault is never guessed: an unset or non-existent path is a
    configuration error, because silently falling back would let the
    Supervisor route against the wrong skill corpus.
    """

    vault_path = os.environ.get(
        VAULT_PATH_ENV
    )

    if not vault_path or not vault_path.strip():
        raise ValueError(
            f"{VAULT_PATH_ENV} environment variable is not set."
        )

    vault_path = vault_path.strip()

    if not Path(vault_path).is_dir():
        raise ValueError(
            f"{VAULT_PATH_ENV} does not point to an existing "
            f"directory: {vault_path!r}."
        )

    return vault_path


class Supervisor:
    """
    Runtime responsible for exactly one KernelAI Request.

    The Supervisor owns:
    - request-level agent context, including the Second Brain REGISTRY,
    - Tasks created for the Request,
    - persistent TaskState for each Task,
    - escalation decisions,
    - Task execution batches,
    - Task outcomes.

    Specialized work is delegated to Task Workers.

    The Supervisor decides what work must happen and which domains a
    Task may access. Task Workers decide which methodologies within
    those domains to apply.

    One Supervisor step is the synchronization boundary: it resolves
    control actions, one runnable Task batch executes concurrently,
    and the outcomes return to the next step. Escalations are decided
    by that same step sequence, never by a separate per-Task model
    call.

    The concrete agent implementation is selected through
    KernelAI's agent factory.
    """

    def __init__(
        self,
        request: Request,
        max_iterations: int = 10,
        max_concurrent_tasks: int = 5,
        verbose: bool = False,
        local: bool = False,
        trace: RuntimeTracer | None = None,
        tool_authority: ToolAuthorityResolver | None = None,
    ) -> None:
        self.request = request

        if max_concurrent_tasks < 1:
            raise ValueError(
                "max_concurrent_tasks must be at least 1."
            )

        self.max_iterations = max_iterations
        self.verbose = verbose
        self.local = local
        self.trace = trace

        # Read-only access to the KernelAI Second Brain vault.
        #
        # One runtime serves this entire Request. Domain and skill
        # lookups never construct a second runtime.
        self.second_brain = SecondBrainRuntime(
            _resolve_vault_path()
        )

        # Every Task created during this Request.
        self.tasks: dict[str, Task] = {}

        # Persistent resumable execution state for those Tasks.
        #
        # Task and TaskState are both keyed by task_id: the Task is
        # the work definition, the TaskState is how far a Worker has
        # got in doing it across attempts.
        self.task_states: dict[str, TaskState] = {}

        # Domain -> tool authority for this Request.
        #
        # Tool authority is derived from authorized domains, never
        # stored on TaskState. The default is the trusted system
        # registry; a test may inject an equivalent resolver through
        # the existing seam.
        self.tool_authority = (
            tool_authority
            or TrustedDomainGrants()
        )

        # Escalations that passed deterministic validation and are
        # awaiting a Supervisor decision on a later step.
        self.resolved_escalations: dict[
            str,
            ResolvedEscalation,
        ] = {}

        # Attempt-local restart guidance for replacement Workers.
        #
        # Advisory only, never part of TaskState, and consumed by
        # whoever starts the next attempt.
        self.restart_suggestions: dict[str, str] = {}

        # Tasks runnable in the current Supervisor step.
        #
        # Delegation and escalation resolution both add here, so
        # restarted Tasks and newly delegated Tasks can execute in
        # the same batch.
        self.pending_tasks: list[Task] = []

        # Outcomes returned by child Task Workers.
        self.outcomes: dict[str, TaskOutcome] = {}

        # Runtime responsible for executing child Tasks.
        #
        # It shares this Request's Second Brain runtime and trusted
        # authority source, so execution and escalation can never
        # disagree about authority.
        self.task_executor = TaskExecutor(
            second_brain=self.second_brain,
            tool_authority=self.tool_authority,
            max_iterations=max_iterations,
            verbose=verbose,
            local=local,
            trace=self.trace,
            restart_suggestion=self.take_restart_suggestion,
        )

        self.task_semaphore = asyncio.Semaphore(
            max_concurrent_tasks
        )

        # Supervisor control capabilities.
        control_tools = SupervisorControlTools(
            supervisor=self,
        )

        self.control_client = ControlToolClient(
            tools=control_tools.get_tools(),
        )

        # Request-level agent instructions.
        #
        # The Supervisor starts with its behavioral role plus the
        # global skill REGISTRY. The registry is provided directly:
        # it is not a control tool, and no DOMAIN.md or skill body is
        # injected until a Task is delegated.
        role_prompt = SUPERVISOR_ROLE_PATH.read_text(
            encoding="utf-8"
        )

        registry = self.second_brain.get_registry()

        if isinstance(registry, SecondBrainError):
            raise RuntimeError(
                "Could not load the Second Brain REGISTRY: "
                f"{registry.message}"
            )

        system_prompt = "\n\n".join(
            [
                role_prompt,
                registry,
            ]
        )

        # Create the configured request-level agent.
        #
        # Supervisor does not depend on a concrete agent
        # implementation such as GenericWorker.
        self.agent = create_agent(
            tools=self.control_client,
            system_prompt=system_prompt,
            allowed_tools={
                "delegate_task",
                "resolve_escalation",
            },
            max_iterations=max_iterations,
            verbose=verbose,
            local=local,
            trace=self.trace,
            worker_role="supervisor",
        )

    async def _execute_task(
        self,
        task: Task,
    ) -> TaskOutcome:
        """
        Execute one child Task while respecting this Supervisor's
        concurrent Task limit.
        """

        self._emit(
            event="task_waiting",
            task_id=task.task_id,
        )

        async with self.task_semaphore:
            self._emit(
                event="task_started",
                task_id=task.task_id,
            )

            state = self.task_states.get(
                task.task_id
            )

            if state is None:
                raise RuntimeError(
                    f"Task {task.task_id!r} has no TaskState."
                )

            outcome = await self.task_executor.execute(
                task,
                state,
            )

            task.status = outcome.status

            return outcome

    async def run(
        self,
    ) -> dict[str, Any]:
        """
        Execute the complete lifecycle of this Request.
        """

        self._emit(
            event="supervisor_started",
        )

        try:
            await self.agent.start(
                query=self.request.query,
                request_id=self.request.request_id,
            )

            while not self.agent.is_done:

                self._emit(
                    event="supervisor_step_started",
                )

                # One Supervisor reasoning / control-tool step.
                #
                # This is the only place Task escalation is decided:
                # the model sees every outcome of the previous batch
                # and answers through resolve_escalation().
                await self.agent.run_step()

                self._emit(
                    event="supervisor_step_completed",
                )

                if self.agent.is_done:
                    break

                # No Tasks were delegated or resolved during this step.
                if not self.pending_tasks:
                    continue

                # Detach the current batch before execution.
                #
                # Control actions during a future step therefore
                # belong to a new batch.
                batch = self.pending_tasks
                self.pending_tasks = []

                self._emit(
                    event="batch_started",
                    task_count=len(batch),
                )

                # Execute all runnable Tasks from this step together.
                outcomes = await self._execute_batch(
                    batch
                )

                # Deterministic outcome processing. No model call is
                # made here: BLOCKED Tasks are persisted, validated,
                # and left BLOCKED for the next Supervisor step.
                outcomes = self._process_blocked_outcomes(
                    outcomes
                )

                self._emit(
                    event="batch_completed",
                    task_count=len(outcomes),
                )

                # Give bounded Task outcomes back to the Supervisor.
                self._add_outcomes_to_context(
                    outcomes
                )

            if self.agent.result is None:
                raise RuntimeError(
                    "Supervisor completed without producing a result."
                )

            self._emit(
                event="supervisor_completed",
            )

            return self.agent.result

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            self._emit(
                event="supervisor_failed",
                error=type(exc).__name__,
            )
            raise

    async def _execute_batch(
        self,
        tasks: list[Task],
    ) -> list[TaskOutcome]:
        """
        Execute one runnable batch concurrently and record outcomes.
        """

        outcomes = await asyncio.gather(
            *[
                self._execute_task(task)
                for task in tasks
            ]
        )

        for outcome in outcomes:
            self.outcomes[
                outcome.task_id
            ] = outcome

        return list(outcomes)

    def _process_blocked_outcomes(
        self,
        outcomes: list[TaskOutcome],
    ) -> list[TaskOutcome]:
        """
        Persist and validate every BLOCKED outcome of a batch.

        Runs without any model call. A BLOCKED Task stays BLOCKED so
        the next Supervisor step can decide it; an escalation that
        cannot be honoured deterministically ends the Task instead.
        """

        processed: list[TaskOutcome] = []

        for outcome in outcomes:
            if outcome.status is not TaskStatus.BLOCKED:
                processed.append(outcome)
                continue

            task = self.tasks.get(outcome.task_id)
            state = self.task_states.get(outcome.task_id)

            if task is None or state is None:
                self._emit(
                    event="escalation_unroutable",
                    task_id=outcome.task_id,
                )
                processed.append(outcome)
                continue

            # Persist the Worker's accumulated work before the
            # escalation is exposed.
            #
            # Worker-owned progress is copied verbatim. Control state
            # (attempt, authorized domains, skill limit, loaded
            # skills) is never taken from Worker output.
            apply_outcome(state, outcome)

            task.status = TaskStatus.BLOCKED

            try:
                resolved = validate_escalation(
                    state,
                    state.escalation,
                    domain_exists=self._domain_exists,
                    tool_authority=self.tool_authority,
                )

            except EscalationValidationError as exc:
                # Malformed authority requests never reach the model.
                processed.append(
                    self._terminate_blocked_task(
                        task=task,
                        state=state,
                        error=str(exc),
                    )
                )
                continue

            self.resolved_escalations[
                task.task_id
            ] = resolved

            escalation = state.escalation

            self._emit(
                event="escalation_awaiting_decision",
                task_id=task.task_id,
                attempt=state.attempt,
                kind=resolved.kind.value,
                reason=escalation.reason,
                requested_domain=escalation.requested_domain,
                requested_tool=escalation.requested_tool,
                requested_skill_limit=(
                    escalation.requested_skill_limit
                ),
                candidate_domains=list(
                    resolved.candidate_domains
                ),
                grantable=resolved.grantable,
            )

            processed.append(outcome)

        return processed

    def _terminate_blocked_task(
        self,
        *,
        task: Task,
        state: TaskState,
        error: str,
    ) -> TaskOutcome:
        """
        End a BLOCKED Task whose escalation cannot be honoured.

        Progress, findings, artifacts, and remaining work are kept so
        the Request can still synthesize from the partial work.
        """

        task.status = TaskStatus.FAILED

        self.resolved_escalations.pop(
            task.task_id,
            None,
        )
        self.restart_suggestions.pop(
            task.task_id,
            None,
        )

        outcome = TaskOutcome(
            request_id=task.request_id,
            task_id=task.task_id,
            status=TaskStatus.FAILED,
            error=error,
            progress=state.progress,
            findings=tuple(state.findings),
            artifacts=tuple(state.artifacts),
            remaining_work=state.remaining_work,
            escalation=state.escalation,
        )

        self.outcomes[task.task_id] = outcome

        self._emit(
            event="escalation_rejected",
            task_id=task.task_id,
            attempt=state.attempt,
            error=error,
        )

        return outcome

    def take_restart_suggestion(
        self,
        task_id: str,
    ) -> str | None:
        """
        Consume the attempt-local restart guidance for one Task.

        The guidance is advisory: it never changes authority, limits,
        or accumulated work. Whoever starts the next attempt reads it
        here; it is not part of TaskState.
        """

        return self.restart_suggestions.pop(
            task_id,
            None,
        )

    def _domain_exists(
        self,
        domain: str,
    ) -> bool:
        """
        Report whether a domain exists in the Second Brain vault.
        """

        result = self.second_brain.get_domain(domain)

        return not isinstance(
            result,
            SecondBrainError,
        )

    def _select_tool_domain(
        self,
        candidates: tuple[str, ...],
        state: TaskState,
    ) -> str:
        """
        Choose the single domain granted for a requested tool.

        Several trusted domains may provide a tool, but tool authority
        expands through domains, so exactly one is granted: the
        minimum authority that makes the tool available.
        """

        return min(
            candidates,
            key=lambda domain: (
                len(
                    self.tool_authority.tools_for_domain(
                        domain
                    )
                ),
                domain,
            ),
        )

    def _add_outcomes_to_context(
        self,
        outcomes: list[TaskOutcome],
    ) -> None:
        """
        Add child Task outcomes to the Supervisor's request context.

        Completed, failed, and blocked outcomes of the batch are added
        together, so one Supervisor step sees all of them at once.

        Only bounded Task state is propagated upward: never Worker
        conversation, reasoning, or raw trace.
        """

        serialized_outcomes = []

        for outcome in outcomes:
            task = self.tasks.get(outcome.task_id)
            state = self.task_states.get(outcome.task_id)

            # Report the Task's lifecycle status: a BLOCKED attempt
            # whose escalation was rejected ends FAILED, and must not
            # read as resumable.
            status = (
                task.status
                if task is not None
                else outcome.status
            )

            entry: dict[str, Any] = {
                "task_id": outcome.task_id,
                "status": status.value,
                "result": outcome.result,
                "error": outcome.error,
            }

            if (
                outcome.status is TaskStatus.BLOCKED
                and state is not None
            ):
                entry.update(
                    {
                        "attempt": state.attempt,
                        "instruction": state.instruction,
                        "authorized_domains": sorted(
                            state.authorized_domains
                        ),
                        "skill_limit": state.skill_limit,
                        "loaded_skills": list(
                            state.loaded_skills
                        ),
                        "progress": state.progress,
                        "findings": list(state.findings),
                        "artifacts": list(state.artifacts),
                        "remaining_work": (
                            state.remaining_work
                        ),
                    }
                )

                escalation = state.escalation

                if escalation is not None:
                    entry["escalation"] = {
                        "kind": escalation.kind.value,
                        "reason": escalation.reason,
                        "requested_domain": (
                            escalation.requested_domain
                        ),
                        "requested_tool": (
                            escalation.requested_tool
                        ),
                        "requested_skill_limit": (
                            escalation.requested_skill_limit
                        ),
                    }

                resolved = self.resolved_escalations.get(
                    outcome.task_id
                )

                if (
                    resolved is not None
                    and resolved.candidate_domains
                ):
                    entry["candidate_domains"] = list(
                        resolved.candidate_domains
                    )

            serialized_outcomes.append(entry)

        self.agent.messages.append(
            {
                "role": "user",
                "content": (
                    "The delegated Tasks from the previous "
                    "planning step have finished.\n\n"
                    + json.dumps(
                        serialized_outcomes,
                        default=str,
                    )
                ),
            }
        )

    def _emit(
        self,
        event: str,
        task_id: str | None = None,
        **data: Any,
    ) -> None:
        """
        Emit one Supervisor lifecycle event.
        """

        if self.trace is None:
            return

        self.trace.emit(
            component="supervisor",
            event=event,
            request_id=self.request.request_id,
            task_id=task_id,
            **data,
        )
