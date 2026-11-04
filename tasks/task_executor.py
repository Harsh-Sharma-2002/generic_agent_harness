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

import json
from pathlib import Path
from typing import Any, Callable

from observability.tracer import RuntimeTracer
from second_brain.models import SecondBrainError
from second_brain.runtime import SecondBrainRuntime
from tasks.models import (
    EscalationKind,
    EscalationRequest,
    TaskState,
)
from tasks.outcome import TaskOutcome
from tasks.task import Task, TaskStatus
from tool_grants.models import ToolAuthorityResolver
from worker.clients.base import ToolClient
from worker.clients.mcp import MCPToolClient
from worker.factory import create_agent
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

        except ValueError as exc:
            task.status = TaskStatus.FAILED

            outcome = TaskOutcome(
                request_id=task.request_id,
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=(
                    "Invalid structured Worker result: "
                    f"{exc}"
                ),
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
        Convert a structured Worker result into canonical TaskOutcome.

        Raises ValueError for malformed output. Nothing is inferred
        from prose, and no authority is ever derived from it.
        """

        payload = self._structured_payload(
            agent_result
        )

        raw_status = payload.get("status")

        if not isinstance(raw_status, str):
            raise ValueError(
                "Worker result requires a string 'status'."
            )

        status = raw_status.strip().lower()

        result = payload.get("result")

        if result is not None and not isinstance(
            result,
            dict,
        ):
            raise ValueError(
                "Worker result 'result' must be an object or null."
            )

        progress = self._optional_string(
            payload,
            "progress",
        )
        remaining_work = self._optional_string(
            payload,
            "remaining_work",
        )
        findings = self._string_list(
            payload,
            "findings",
        )
        artifacts = self._string_list(
            payload,
            "artifacts",
        )

        if status == "completed":
            if payload.get("escalation") is not None:
                raise ValueError(
                    "A completed Worker result must not carry an "
                    "escalation."
                )

            return TaskOutcome(
                request_id=task.request_id,
                task_id=task.task_id,
                status=TaskStatus.COMPLETED,
                result=result,
                progress=progress,
                findings=tuple(findings),
                artifacts=tuple(artifacts),
                remaining_work=remaining_work,
                escalation=None,
            )

        if status == "blocked":
            escalation = self._parse_escalation(
                payload.get("escalation")
            )

            return TaskOutcome(
                request_id=task.request_id,
                task_id=task.task_id,
                status=TaskStatus.BLOCKED,
                result=result,
                progress=progress,
                findings=tuple(findings),
                artifacts=tuple(artifacts),
                remaining_work=remaining_work,
                escalation=escalation,
            )

        raise ValueError(
            f"Unknown Worker status {raw_status!r}."
        )

    @staticmethod
    def _structured_payload(
        agent_result: Any,
    ) -> dict[str, Any]:
        """
        Extract the structured Worker payload.

        A structured agent may return the payload directly. The
        GenericWorker returns ``{"content": "..."}``, so that content
        must be exactly one JSON object.
        """

        if not isinstance(agent_result, dict):
            raise ValueError(
                "Worker result must be an object."
            )

        if "status" in agent_result:
            return agent_result

        content = agent_result.get("content")

        if (
            not isinstance(content, str)
            or not content.strip()
        ):
            raise ValueError(
                "Worker result contains no structured payload."
            )

        text = content.strip()

        # Tolerate a single fenced JSON block; that is formatting, not
        # semantics.
        if text.startswith("```"):
            lines = text.splitlines()

            if (
                len(lines) >= 2
                and lines[-1].strip() == "```"
            ):
                lines = lines[1:-1]

                if (
                    lines
                    and lines[0].strip().lower()
                    in {"json", "jsonc"}
                ):
                    lines = lines[1:]

                text = "\n".join(lines).strip()

        try:
            payload = json.loads(text)

        except json.JSONDecodeError as exc:
            raise ValueError(
                "Worker result is not valid JSON: "
                f"{exc.msg}."
            ) from exc

        if not isinstance(payload, dict):
            raise ValueError(
                "Worker result JSON must be an object."
            )

        return payload

    @staticmethod
    def _optional_string(
        payload: dict[str, Any],
        field: str,
    ) -> str:
        value = payload.get(field, "")

        if value is None:
            return ""

        if not isinstance(value, str):
            raise ValueError(
                f"Worker result '{field}' must be a string."
            )

        return value

    @staticmethod
    def _string_list(
        payload: dict[str, Any],
        field: str,
    ) -> list[str]:
        value = payload.get(field, [])

        if value is None:
            return []

        if not isinstance(value, list) or any(
            not isinstance(item, str)
            for item in value
        ):
            raise ValueError(
                f"Worker result '{field}' must be a list of "
                "strings."
            )

        return list(value)

    @staticmethod
    def _parse_escalation(
        raw: Any,
    ) -> EscalationRequest:
        """
        Build the canonical escalation from strict structure.
        """

        if not isinstance(raw, dict):
            raise ValueError(
                "A blocked Worker result requires an escalation "
                "object."
            )

        raw_kind = raw.get("kind")

        if not isinstance(raw_kind, str):
            raise ValueError(
                "Escalation requires a string 'kind'."
            )

        try:
            kind = EscalationKind(
                raw_kind.strip().lower()
            )

        except ValueError as exc:
            raise ValueError(
                f"Unknown escalation kind {raw_kind!r}."
            ) from exc

        reason = raw.get("reason")

        if (
            not isinstance(reason, str)
            or not reason.strip()
        ):
            raise ValueError(
                "Escalation requires a non-empty 'reason'."
            )

        requested_domain = raw.get("requested_domain")
        requested_tool = raw.get("requested_tool")
        requested_skill_limit = raw.get(
            "requested_skill_limit"
        )

        if kind is EscalationKind.DOMAIN_REQUIRED:
            if (
                not isinstance(requested_domain, str)
                or not requested_domain.strip()
            ):
                raise ValueError(
                    "DOMAIN_REQUIRED requires "
                    "'requested_domain'."
                )

            if (
                requested_tool is not None
                or requested_skill_limit is not None
            ):
                raise ValueError(
                    "DOMAIN_REQUIRED must not carry other "
                    "request fields."
                )

            requested_domain = requested_domain.strip()

        elif kind is EscalationKind.TOOL_REQUIRED:
            if (
                not isinstance(requested_tool, str)
                or not requested_tool.strip()
            ):
                raise ValueError(
                    "TOOL_REQUIRED requires 'requested_tool'."
                )

            if (
                requested_domain is not None
                or requested_skill_limit is not None
            ):
                raise ValueError(
                    "TOOL_REQUIRED must not carry other "
                    "request fields."
                )

            requested_tool = requested_tool.strip()

        else:
            if (
                not isinstance(requested_skill_limit, int)
                or isinstance(requested_skill_limit, bool)
            ):
                raise ValueError(
                    "SKILL_LIMIT_EXCEEDED requires an integer "
                    "'requested_skill_limit'."
                )

            if (
                requested_domain is not None
                or requested_tool is not None
            ):
                raise ValueError(
                    "SKILL_LIMIT_EXCEEDED must not carry other "
                    "request fields."
                )

        return EscalationRequest(
            kind=kind,
            reason=reason.strip(),
            requested_domain=requested_domain,
            requested_tool=requested_tool,
            requested_skill_limit=requested_skill_limit,
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
