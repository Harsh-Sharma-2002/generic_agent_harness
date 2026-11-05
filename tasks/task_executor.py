"""Execute one KernelAI Task attempt from persistent TaskState.

TaskExecutor owns the boundary between a semantic Task and one
disposable Worker attempt. It does not schedule work, own TaskState,
or decide authority: it projects the current resumable state into a
fresh Worker.

Authority is read from the attempt's current state:

    TaskState.authorized_domains
            +
    TrustedDomainGrants
            ↓
    allowed_tools

Derived tools are ephemeral. Nothing here writes authority back to
Task or TaskState, and nothing here derives tools from a DOMAIN.md or
a methodology document.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

from observability.tracer import RuntimeTracer
from second_brain.models import SecondBrainError
from second_brain.runtime import SecondBrainRuntime
from tasks.models import TaskState
from tasks.outcome import TaskOutcome
from tasks.task import Task, TaskStatus
from tool_grants.models import ToolAuthorityResolver
from worker.clients.base import ToolClient
from worker.clients.mcp import MCPToolClient
from worker.factory import create_agent
from worker.message import (
    InvalidWorkerResult,
    WORKER_TASK_RESULT_RESPONSE_FORMAT,
    WorkerIterationExhausted,
    WorkerTaskResult,
)
from worker.skill_runtime import (
    SkillToolClient,
    WorkerSkillRuntime,
)


TASK_WORKER_ROLE_PATH = (
    Path(__file__).with_name("task_worker.md")
)

#: Runtime control capabilities exposed to a Task Worker.
#:
#: These are attempt infrastructure, not execution authority, so they
#: never appear in Trusted Domain Grants.
WORKER_CONTROL_TOOLS = frozenset({"get_skill"})


class TaskExecutor:
    """
    Construct and run one disposable Worker per Task attempt.

    Dependencies are owned by the Supervisor for the whole Request and
    injected here: the TaskExecutor never builds a second Second
    Brain runtime or a second authority registry.
    """

    def __init__(
        self,
        second_brain: SecondBrainRuntime,
        tool_authority: ToolAuthorityResolver,
        max_iterations: int = 10,
        verbose: bool = False,
        local: bool = False,
        trace: RuntimeTracer | None = None,
        tool_client: ToolClient | None = None,
        restart_suggestion: (
            Callable[[str], str | None] | None
        ) = None,
    ) -> None:
        self.second_brain = second_brain
        self.tool_authority = tool_authority

        self.max_iterations = max_iterations
        self.verbose = verbose
        self.local = local
        self.trace = trace

        # Execution tool client. Created lazily so TaskExecutor
        # construction never requires MCP configuration.
        self.tool_client = tool_client

        # Attempt-local Supervisor guidance. Called only when an
        # attempt is actually being constructed, so a failed
        # validation does not consume it.
        self.restart_suggestion = restart_suggestion

        self.task_worker_role = TASK_WORKER_ROLE_PATH.read_text(
            encoding="utf-8"
        )

    async def execute(
        self,
        task: Task,
        task_state: TaskState,
    ) -> TaskOutcome:
        """
        Execute one queued Task attempt and return its immutable
        outcome.

        The caller owns TaskState. Passing the same object across
        attempts is what makes progress resumable.

        This is also the attempt-failure boundary. Contract violations
        (wrong Task status, Task/TaskState identity mismatch) still
        raise, because they are programming errors. Every other
        attempt failure — DOMAIN loading, tool discovery, trusted-tool
        validation, Worker construction, provider interaction — becomes
        a FAILED TaskOutcome, so one Task can never abort its siblings
        or the Request. Cancellation always propagates.
        """

        if task.status is not TaskStatus.QUEUED:
            raise ValueError(
                f"Task {task.task_id!r} cannot be executed "
                f"from status {task.status.value!r}."
            )

        self._validate_attempt_identity(
            task,
            task_state,
        )

        try:
            return await self._run_attempt(
                task,
                task_state,
            )

        except asyncio.CancelledError:
            raise

        except WorkerIterationExhausted as exc:
            return self._exhausted_outcome(
                task=task,
                task_state=task_state,
                exhausted=exc,
            )

        except Exception as exc:
            return self._failed_outcome(
                task=task,
                task_state=task_state,
                error=exc,
            )

    async def _run_attempt(
        self,
        task: Task,
        task_state: TaskState,
    ) -> TaskOutcome:
        """Run the attempt body; execute() converts its failures."""

        # Current authority, not the originally delegated authority.
        authorized_domains = sorted(
            task_state.authorized_domains
        )

        if not authorized_domains:
            raise ValueError(
                f"Task {task.task_id!r} has no authorized "
                "domains."
            )

        # Load every authorized DOMAIN document through the Second
        # Brain runtime. A missing document is a configuration error,
        # never a silent omission.
        domain_documents = self._load_domain_documents(
            task=task,
            authorized_domains=authorized_domains,
        )

        # Derive the attempt's execution tools from current
        # authority only.
        execution_tools = self._derive_allowed_tools(
            authorized_domains
        )

        tool_client = (
            self.tool_client
            if self.tool_client is not None
            else MCPToolClient()
        )

        await self._validate_runtime_tools(
            task=task,
            allowed_tools=execution_tools,
            tool_client=tool_client,
        )

        # Consume one-shot guidance only now: the attempt is valid and
        # about to be constructed.
        suggestion = self._take_suggestion(task.task_id)

        system_prompt = self._build_system_prompt(
            task_state=task_state,
            domain_documents=domain_documents,
            suggestion=suggestion,
        )

        # get_skill is composed onto the attempt's tool client. It is
        # runtime control capability and never comes from a trusted
        # domain grant.
        skill_runtime = WorkerSkillRuntime(
            second_brain=self.second_brain,
            task_state=task_state,
            attempt=task_state.attempt,
            request_id=task.request_id,
            task_id=task.task_id,
            trace=self.trace,
        )

        worker_tools = SkillToolClient(
            tools=tool_client,
            skill_runtime=skill_runtime,
        )

        allowed_tools = (
            execution_tools | WORKER_CONTROL_TOOLS
        )

        # A fresh Worker per attempt: no conversation, tool state, or
        # context is reused from a previous attempt.
        agent = create_agent(
            tools=worker_tools,
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            max_iterations=self.max_iterations,
            verbose=self.verbose,
            local=self.local,
            trace=self.trace,
            worker_role="task",
            final_response_format=(
                WORKER_TASK_RESULT_RESPONSE_FORMAT
            ),
        )

        task.status = TaskStatus.RUNNING

        self._emit(
            event="attempt_started",
            task=task,
            attempt=task_state.attempt,
            authorized_domains=authorized_domains,
            execution_tools=sorted(execution_tools),
            control_tools=sorted(WORKER_CONTROL_TOOLS),
            allowed_tools=sorted(allowed_tools),
            loaded_skills=list(task_state.loaded_skills),
        )

        try:
            result = await agent.run(
                query=task.query,
                request_id=task.request_id,
                task_id=task.task_id,
            )

        except InvalidWorkerResult as exc:
            # Structured finalization crossed its own boundary and
            # failed. This is an explicit attempt failure: prose is
            # never rescued into a lifecycle state.
            task.status = TaskStatus.FAILED

            self._emit(
                event="worker_result_invalid",
                task=task,
                attempt=task_state.attempt,
                error=str(exc),
            )

            self._emit(
                event="task_failed",
                task=task,
                attempt=task_state.attempt,
                error="InvalidWorkerResult",
            )

            return TaskOutcome(
                request_id=task.request_id,
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                result=None,
                error=f"Invalid Worker result: {exc}",
            )

        except (ValueError, RuntimeError) as exc:
            task.status = TaskStatus.FAILED

            outcome = TaskOutcome(
                request_id=task.request_id,
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                result=None,
                error=str(exc),
            )

            self._emit(
                event="task_failed",
                task=task,
                attempt=task_state.attempt,
                error=type(exc).__name__,
            )

            return outcome

        # TaskExecutor is the Worker-attempt -> TaskOutcome boundary.
        # Lifecycle is taken from the structured payload, never from
        # keywords in prose.
        try:
            outcome = self._outcome_from_worker_result(
                task,
                result,
            )

        except InvalidWorkerResult as exc:
            task.status = TaskStatus.FAILED

            outcome = TaskOutcome(
                request_id=task.request_id,
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=f"Invalid Worker result: {exc}",
            )

            self._emit(
                event="worker_result_invalid",
                task=task,
                attempt=task_state.attempt,
                error=str(exc),
            )

            self._emit(
                event="task_failed",
                task=task,
                attempt=task_state.attempt,
                error="InvalidWorkerResult",
            )

            return outcome

        task.status = outcome.status

        self._emit(
            event=(
                "task_completed"
                if outcome.status is TaskStatus.COMPLETED
                else "task_blocked"
            ),
            task=task,
            attempt=task_state.attempt,
            status=outcome.status.value,
            escalation=(
                outcome.escalation.kind.value
                if outcome.escalation is not None
                else None
            ),
        )

        return outcome

    def _validate_attempt_identity(
        self,
        task: Task,
        task_state: TaskState,
    ) -> None:
        """
        Refuse to run an attempt whose Task and TaskState disagree.

        The Task is the semantic definition; TaskState is the same
        Task's execution state. Disagreement is an invariant
        violation, not something to guess around.
        """

        if task.task_id != task_state.task_id:
            raise ValueError(
                "Task and TaskState task_id disagree: "
                f"{task.task_id!r} != "
                f"{task_state.task_id!r}."
            )

        if (
            task.query.strip()
            != task_state.instruction.strip()
        ):
            raise ValueError(
                f"Task {task.task_id!r} instruction and "
                "TaskState instruction disagree; the original "
                "Task instruction is authoritative."
            )

    def _load_domain_documents(
        self,
        *,
        task: Task,
        authorized_domains: list[str],
    ) -> dict[str, str]:
        """
        Load each authorized DOMAIN document, deterministically.
        """

        documents: dict[str, str] = {}

        for domain in authorized_domains:

            document = self.second_brain.get_domain(
                domain
            )

            if isinstance(
                document,
                SecondBrainError,
            ):
                raise RuntimeError(
                    "Authorized domain "
                    f"{domain!r} for Task {task.task_id!r} "
                    "could not be loaded: "
                    f"{document.message}"
                )

            documents[domain] = document

        return documents

    def _derive_allowed_tools(
        self,
        authorized_domains: list[str],
    ) -> set[str]:
        """
        Derive this attempt's tools from its current domains.

        The result is a local, ephemeral set: it is never written to
        TaskState.
        """

        allowed_tools: set[str] = set()

        for domain in authorized_domains:
            allowed_tools.update(
                self.tool_authority.tools_for_domain(
                    domain
                )
            )

        return allowed_tools

    async def _validate_runtime_tools(
        self,
        *,
        task: Task,
        allowed_tools: set[str],
        tool_client: ToolClient,
    ) -> None:
        """
        Verify every derived tool exists in the current runtime.

        A trusted grant that names an unavailable tool is a system
        configuration error. Authority is never broadened or silently
        dropped as a fallback.
        """

        schemas = await tool_client.get_tools()

        available_tools = {
            schema["function"]["name"]
            for schema in schemas
        }

        unavailable = sorted(
            allowed_tools - available_tools
        )

        if unavailable:
            raise RuntimeError(
                f"Task {task.task_id!r} trusted grants "
                "reference unavailable runtime tool(s): "
                + ", ".join(unavailable)
            )

    def _take_suggestion(
        self,
        task_id: str,
    ) -> str | None:
        """
        Consume the attempt-local Supervisor guidance, if any.
        """

        if self.restart_suggestion is None:
            return None

        suggestion = self.restart_suggestion(
            task_id
        )

        if suggestion is None:
            return None

        suggestion = suggestion.strip()

        return suggestion or None

    def _build_system_prompt(
        self,
        *,
        task_state: TaskState,
        domain_documents: dict[str, str],
        suggestion: str | None,
    ) -> str:
        """
        Compose the Worker's deterministic attempt context.
        """

        sections = [
            self.task_worker_role,
            self._domains_section(domain_documents),
        ]

        resumable = self._resumable_section(
            task_state
        )

        if resumable is not None:
            sections.append(resumable)

        if suggestion is not None:
            sections.append(
                "## Supervisor Guidance\n\n"
                + suggestion
            )

        return "\n\n---\n\n".join(sections)

    def _domains_section(
        self,
        domain_documents: dict[str, str],
    ) -> str:
        """
        Project only the authorized DOMAIN documents.
        """

        blocks = [
            f"### Domain: {domain}\n\n"
            + document.strip()
            for domain, document in sorted(
                domain_documents.items()
            )
        ]

        return (
            "## Authorized Domains\n\n"
            + "\n\n".join(blocks)
        )

    def _resumable_section(
        self,
        task_state: TaskState,
    ) -> str | None:
        """
        Render bounded resumable state for a continued attempt.

        Nothing conversational, no reasoning, and no trace: only the
        operational state another Worker needs to continue.
        """

        has_work = bool(
            task_state.progress.strip()
            or task_state.findings
            or task_state.artifacts
            or task_state.remaining_work.strip()
            or task_state.loaded_skills
        )

        if task_state.attempt <= 1 and not has_work:
            return None

        lines = [
            "## Resumable Task State",
            "",
            f"Attempt: {task_state.attempt}",
        ]

        if task_state.progress.strip():
            lines.extend(
                [
                    "",
                    "Progress:",
                    task_state.progress.strip(),
                ]
            )

        if task_state.findings:
            lines.extend(
                [
                    "",
                    "Findings:",
                    *[
                        f"- {finding}"
                        for finding in task_state.findings
                    ],
                ]
            )

        if task_state.artifacts:
            lines.extend(
                [
                    "",
                    "Artifacts:",
                    *[
                        f"- {artifact}"
                        for artifact in task_state.artifacts
                    ],
                ]
            )

        if task_state.remaining_work.strip():
            lines.extend(
                [
                    "",
                    "Remaining work:",
                    task_state.remaining_work.strip(),
                ]
            )

        if task_state.loaded_skills:
            lines.extend(
                [
                    "",
                    "Previously loaded methodologies:",
                    *[
                        f"- {skill}"
                        for skill in task_state.loaded_skills
                    ],
                ]
            )

        return "\n".join(lines)

    def _outcome_from_worker_result(
        self,
        task: Task,
        agent_result: Any,
    ) -> TaskOutcome:
        """
        Convert a canonical WorkerTaskResult into canonical TaskOutcome.

        The Worker has already crossed the structured boundary. This
        boundary only maps lifecycle and accumulated work: it never
        parses prose, rescues malformed output, or derives authority.
        """

        if not isinstance(agent_result, WorkerTaskResult):
            raise InvalidWorkerResult(
                "Task Worker returned an unstructured result; "
                "the configured Worker must return a canonical "
                "WorkerTaskResult."
            )

        if agent_result.status == "completed":
            return TaskOutcome(
                request_id=task.request_id,
                task_id=task.task_id,
                status=TaskStatus.COMPLETED,
                result=agent_result.result,
                progress=agent_result.progress,
                findings=tuple(agent_result.findings),
                artifacts=tuple(agent_result.artifacts),
                remaining_work=agent_result.remaining_work,
                escalation=None,
            )

        return TaskOutcome(
            request_id=task.request_id,
            task_id=task.task_id,
            status=TaskStatus.BLOCKED,
            result=agent_result.result,
            progress=agent_result.progress,
            findings=tuple(agent_result.findings),
            artifacts=tuple(agent_result.artifacts),
            remaining_work=agent_result.remaining_work,
            escalation=agent_result.escalation,
        )

    def _failed_outcome(
        self,
        *,
        task: Task,
        task_state: TaskState,
        error: Exception,
    ) -> TaskOutcome:
        """Convert an attempt-runtime failure into a FAILED outcome.

        Prior accumulated work is preserved verbatim; nothing is
        fabricated and no authority is changed.
        """

        task.status = TaskStatus.FAILED

        message = str(error)

        if len(message) > 300:
            message = message[:300] + "..."

        self._emit(
            event="task_failed",
            task=task,
            attempt=task_state.attempt,
            error=type(error).__name__,
        )

        return TaskOutcome(
            request_id=task.request_id,
            task_id=task.task_id,
            status=TaskStatus.FAILED,
            error=message,
            progress=task_state.progress,
            findings=tuple(task_state.findings),
            artifacts=tuple(task_state.artifacts),
            remaining_work=task_state.remaining_work,
        )

    def _exhausted_outcome(
        self,
        *,
        task: Task,
        task_state: TaskState,
        exhausted: WorkerIterationExhausted,
    ) -> TaskOutcome:
        """Record an execution-budget exhaustion as FAILED.

        The packaging step may still have extracted partial work, and
        that work is preserved, but the lifecycle is runtime-owned: an
        exhausted attempt can never be COMPLETED and is not BLOCKED
        because no canonical authority escalation was produced.
        """

        task.status = TaskStatus.FAILED

        partial = exhausted.result

        findings = list(task_state.findings)
        artifacts = list(task_state.artifacts)

        if partial is not None:
            findings.extend(
                finding
                for finding in partial.findings
                if finding not in findings
            )
            artifacts.extend(
                artifact
                for artifact in partial.artifacts
                if artifact not in artifacts
            )

        self._emit(
            event="task_failed",
            task=task,
            attempt=task_state.attempt,
            error="IterationBudgetExhausted",
        )

        return TaskOutcome(
            request_id=task.request_id,
            task_id=task.task_id,
            status=TaskStatus.FAILED,
            result=(
                partial.result
                if partial is not None
                else None
            ),
            error=str(exhausted),
            progress=(
                partial.progress
                if partial is not None and partial.progress.strip()
                else task_state.progress
            ),
            findings=tuple(findings),
            artifacts=tuple(artifacts),
            remaining_work=(
                partial.remaining_work
                if partial is not None and partial.remaining_work.strip()
                else task_state.remaining_work
            ),
        )

    def _emit(
        self,
        event: str,
        task: Task,
        **data: Any,
    ) -> None:
        """
        Emit one TaskExecutor lifecycle event.
        """

        if self.trace is None:
            return

        self.trace.emit(
            component="task_executor",
            event=event,
            request_id=task.request_id,
            task_id=task.task_id,
            **data,
        )
