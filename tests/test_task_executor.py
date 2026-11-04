"""Deterministic tests for TaskExecutor attempt construction.

No LLM, MCP server, or network is used: the agent factory, tool client
and Second Brain are injected or faked.
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from second_brain.runtime import SecondBrainRuntime
from supervisor.supervisor import Supervisor
from tasks.models import EscalationKind, TaskState
from tasks.request import Request
from tasks.task import Task, TaskStatus
from tasks.task_executor import TaskExecutor
from tool_grants.registry import TrustedDomainGrants


VAULT_PATH = Path(__file__).resolve().parents[1] / "vault"

EXECUTOR_PATH = (
    Path(__file__).resolve().parents[1]
    / "tasks"
    / "task_executor.py"
)

TASK_ID = "task_executor"

INSTRUCTION = "Fix the parser."


class FakeToolClient:
    """A ToolClient whose runtime tool catalog is chosen per test."""

    def __init__(
        self,
        names: set[str] | None = None,
    ) -> None:
        self.names = (
            {"shell", "web_search", "sql_executor"}
            if names is None
            else set(names)
        )
        self.discoveries = 0

    async def get_tools(
        self,
    ) -> list[dict[str, Any]]:
        self.discoveries += 1

        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": "",
                    "parameters": {},
                },
            }
            for name in sorted(self.names)
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        raise NotImplementedError


class FakeAgent:
    """Minimal Worker double recording what the attempt asked it to do."""

    def __init__(
        self,
        result: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = (
            {
                "status": "completed",
                "result": {"content": "done"},
            }
            if result is None
            else result
        )
        self.error = error
        self.queries: list[str] = []
        self.runs = 0
        self.tools: Any = None

    async def run(
        self,
        query: str,
        request_id: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        self.runs += 1
        self.queries.append(query)

        if self.error is not None:
            raise self.error

        return self.result


class AgentFactory:
    """create_agent replacement that records attempt construction."""

    def __init__(
        self,
        agents: list[FakeAgent],
    ) -> None:
        self.agents = list(agents)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        **kwargs: Any,
    ) -> FakeAgent:
        self.calls.append(kwargs)
        agent = self.agents.pop(0)
        agent.tools = kwargs["tools"]
        return agent


class RecordingTrace:
    """Minimal tracer recording emitted lifecycle events."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(
        self,
        **kwargs: Any,
    ) -> None:
        self.events.append(kwargs)


def make_task(
    *,
    domains: tuple[str, ...] = ("coding",),
    instruction: str = INSTRUCTION,
    status: TaskStatus = TaskStatus.QUEUED,
) -> Task:
    return Task(
        request_id="req-task-executor",
        task_id=TASK_ID,
        query=instruction,
        authorized_domains=list(domains),
        status=status,
    )


def make_state(
    *,
    domains: tuple[str, ...] = ("coding",),
    instruction: str = INSTRUCTION,
    attempt: int = 1,
    **kwargs: Any,
) -> TaskState:
    return TaskState(
        task_id=TASK_ID,
        instruction=instruction,
        attempt=attempt,
        authorized_domains=set(domains),
        **kwargs,
    )


def make_executor(
    monkeypatch: pytest.MonkeyPatch,
    factory: AgentFactory,
    *,
    tool_client: FakeToolClient | None = None,
    trace: Any = None,
    restart_suggestion: Any = None,
    second_brain: SecondBrainRuntime | None = None,
) -> TaskExecutor:
    monkeypatch.setattr(
        "tasks.task_executor.create_agent",
        factory,
    )

    return TaskExecutor(
        second_brain=(
            second_brain
            if second_brain is not None
            else SecondBrainRuntime(VAULT_PATH)
        ),
        tool_authority=TrustedDomainGrants(),
        tool_client=tool_client or FakeToolClient(),
        trace=trace,
        restart_suggestion=restart_suggestion,
        local=True,
    )


# --------------------------------------------------------------------
# Attempt identity and lifecycle
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attempt_accepts_task_and_task_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory(
        [
            FakeAgent(
                {
                    "status": "completed",
                    "result": {"content": "fixed"},
                }
            )
        ]
    )

    executor = make_executor(monkeypatch, factory)

    task = make_task()
    state = make_state()

    outcome = await executor.execute(task, state)

    assert outcome.status is TaskStatus.COMPLETED
    assert outcome.result == {"content": "fixed"}
    assert outcome.error is None

    assert task.status is TaskStatus.COMPLETED
    assert outcome.task_id == task.task_id == state.task_id


@pytest.mark.asyncio
async def test_task_must_be_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory([FakeAgent()])
    executor = make_executor(monkeypatch, factory)

    task = make_task(status=TaskStatus.COMPLETED)

    with pytest.raises(ValueError, match="cannot be executed"):
        await executor.execute(task, make_state())

    assert factory.calls == []


@pytest.mark.asyncio
async def test_instruction_mismatch_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory([FakeAgent()])
    executor = make_executor(monkeypatch, factory)

    task = make_task()
    state = make_state(instruction="Do something else.")

    with pytest.raises(ValueError, match="instruction disagree"):
        await executor.execute(task, state)

    assert factory.calls == []


@pytest.mark.asyncio
async def test_task_id_mismatch_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory([FakeAgent()])
    executor = make_executor(monkeypatch, factory)

    state = make_state()
    state.task_id = "task_other"

    with pytest.raises(ValueError, match="task_id disagree"):
        await executor.execute(make_task(), state)

    assert factory.calls == []


@pytest.mark.asyncio
async def test_worker_failure_becomes_failed_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory(
        [FakeAgent(error=RuntimeError("boom"))]
    )

    executor = make_executor(monkeypatch, factory)

    task = make_task()

    outcome = await executor.execute(task, make_state())

    assert outcome.status is TaskStatus.FAILED
    assert outcome.error == "boom"
    assert task.status is TaskStatus.FAILED


# --------------------------------------------------------------------
# Authority comes from TaskState
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_current_authority_comes_from_task_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory([FakeAgent()])
    executor = make_executor(monkeypatch, factory)

    # Delegated authority only covers coding; escalation since then
    # expanded the persistent state to include research.
    task = make_task(domains=("coding",))
    state = make_state(
        domains=("coding", "research"),
    )

    await executor.execute(task, state)

    call = factory.calls[0]

    assert call["allowed_tools"] == {
        "get_skill",
        "shell",
        "web_search",
    }

    prompt = call["system_prompt"]

    assert "### Domain: coding" in prompt
    assert "### Domain: research" in prompt
    assert "### Domain: data" not in prompt
    assert "### Domain: teaching" not in prompt

    # The original Task definition is not mutated to expose current
    # authority.
    assert task.authorized_domains == ["coding"]


@pytest.mark.asyncio
async def test_allowed_tools_are_union_of_trusted_grants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory([FakeAgent()])
    executor = make_executor(monkeypatch, factory)

    await executor.execute(
        make_task(domains=("coding", "data")),
        make_state(domains=("coding", "data")),
    )

    assert factory.calls[0]["allowed_tools"] == {
        "get_skill",
        "shell",
        "sql_executor",
    }


@pytest.mark.asyncio
async def test_allowed_tools_are_not_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory([FakeAgent()])
    executor = make_executor(monkeypatch, factory)

    state = make_state()

    await executor.execute(make_task(), state)

    assert not hasattr(state, "allowed_tools")

    assert "allowed_tools" not in {
        field.name for field in fields(TaskState)
    }

    # Only the domain authority is persisted, unchanged.
    assert state.authorized_domains == {"coding"}


def test_task_state_has_no_tool_authority_fields() -> None:
    names = {field.name for field in fields(TaskState)}

    for forbidden in (
        "allowed_tools",
        "tools",
        "approved_tools",
        "extra_tools",
        "tool_grants",
        "capabilities",
    ):
        assert forbidden not in names


# --------------------------------------------------------------------
# DOMAIN projections
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authorized_domain_loads_through_second_brain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory([FakeAgent()])
    executor = make_executor(monkeypatch, factory)

    await executor.execute(make_task(), make_state())

    prompt = factory.calls[0]["system_prompt"]

    # The real vault document for coding, not REGISTRY.md.
    assert "# Coding" in prompt
    assert "# Skill Registry" not in prompt


@pytest.mark.asyncio
async def test_unauthorized_domains_are_not_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory([FakeAgent()])
    executor = make_executor(monkeypatch, factory)

    await executor.execute(make_task(), make_state())

    prompt = factory.calls[0]["system_prompt"]

    for domain in ("research", "data", "teaching"):
        assert f"### Domain: {domain}" not in prompt


@pytest.mark.asyncio
async def test_multiple_domains_are_loaded_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory(
        [FakeAgent(), FakeAgent()]
    )

    executor = make_executor(monkeypatch, factory)

    # Same authority expressed with different set insertion order.
    await executor.execute(
        make_task(domains=("coding", "research")),
        make_state(domains=("research", "coding")),
    )

    await executor.execute(
        make_task(domains=("coding", "research")),
        make_state(domains=("coding", "research")),
    )

    first_prompt = factory.calls[0]["system_prompt"]
    second_prompt = factory.calls[1]["system_prompt"]

    for prompt in (first_prompt, second_prompt):
        assert (
            prompt.index("### Domain: coding")
            < prompt.index("### Domain: research")
        )

    # Set iteration order cannot affect the Worker context.
    assert first_prompt == second_prompt


@pytest.mark.asyncio
async def test_missing_authorized_domain_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    factory = AgentFactory([FakeAgent()])

    executor = make_executor(
        monkeypatch,
        factory,
        second_brain=SecondBrainRuntime(tmp_path),
    )

    task = make_task()

    with pytest.raises(
        RuntimeError,
        match="could not be loaded",
    ):
        await executor.execute(task, make_state())

    assert factory.calls == []
    assert task.status is TaskStatus.QUEUED


@pytest.mark.asyncio
async def test_empty_authorized_domains_fail_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory([FakeAgent()])
    executor = make_executor(monkeypatch, factory)

    with pytest.raises(
        ValueError,
        match="no authorized domains",
    ):
        await executor.execute(
            make_task(domains=()),
            make_state(domains=()),
        )

    assert factory.calls == []


# --------------------------------------------------------------------
# Tool authority
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_domain_content_does_not_grant_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory([FakeAgent()])
    executor = make_executor(monkeypatch, factory)

    await executor.execute(make_task(), make_state())

    prompt = factory.calls[0]["system_prompt"]

    # The DOMAIN document lists methodologies, yet only the domain's
    # trusted grant contributes tools.
    assert "## Skills" in prompt
    assert factory.calls[0]["allowed_tools"] == {
        "get_skill",
        "shell",
    }


@pytest.mark.asyncio
async def test_unavailable_trusted_tool_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory([FakeAgent()])

    executor = make_executor(
        monkeypatch,
        factory,
        tool_client=FakeToolClient({"shell"}),
    )

    task = make_task(domains=("research",))

    with pytest.raises(
        RuntimeError,
        match="unavailable runtime tool",
    ):
        await executor.execute(
            task,
            make_state(domains=("research",)),
        )

    assert factory.calls == []
    assert task.status is TaskStatus.QUEUED


# --------------------------------------------------------------------
# Fresh Worker per attempt
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fresh_worker_created_for_each_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_agent = FakeAgent()
    second_agent = FakeAgent()

    factory = AgentFactory(
        [first_agent, second_agent]
    )

    executor = make_executor(monkeypatch, factory)

    task = make_task()
    state = make_state()

    await executor.execute(task, state)

    task.status = TaskStatus.QUEUED
    state.attempt = 2

    await executor.execute(task, state)

    assert len(factory.calls) == 2
    assert first_agent is not second_agent
    assert first_agent.runs == 1
    assert second_agent.runs == 1

    # The Task and TaskState persist across attempts.
    assert first_agent.queries == [INSTRUCTION]
    assert second_agent.queries == [INSTRUCTION]


@pytest.mark.asyncio
async def test_old_worker_conversation_is_not_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory(
        [FakeAgent(), FakeAgent()]
    )

    executor = make_executor(monkeypatch, factory)

    task = make_task()
    state = make_state()

    await executor.execute(task, state)

    task.status = TaskStatus.QUEUED
    state.attempt = 2

    await executor.execute(task, state)

    second_prompt = factory.calls[1]["system_prompt"]

    # Only bounded state is carried forward, not the prior Worker's
    # conversation, reasoning, or trace.
    assert second_prompt.count("### Domain: coding") == 1
    assert "tool_calls" not in second_prompt
    assert "assistant" not in second_prompt


# --------------------------------------------------------------------
# Resumable state
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_attempt_has_no_resumable_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory([FakeAgent()])
    executor = make_executor(monkeypatch, factory)

    await executor.execute(make_task(), make_state())

    assert (
        "## Resumable Task State"
        not in factory.calls[0]["system_prompt"]
    )


@pytest.mark.asyncio
async def test_resumable_state_reaches_restarted_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory([FakeAgent()])
    executor = make_executor(monkeypatch, factory)

    state = make_state(
        attempt=2,
        progress="Located the parser.",
        findings=["The parser drops unary minus."],
        artifacts=["diff.txt"],
        remaining_work="Handle unary minus.",
        loaded_skills=["coding.systematic-debugging"],
    )

    await executor.execute(make_task(), state)

    prompt = factory.calls[0]["system_prompt"]

    assert "## Resumable Task State" in prompt
    assert "Attempt: 2" in prompt
    assert "Located the parser." in prompt
    assert "- The parser drops unary minus." in prompt
    assert "- diff.txt" in prompt
    assert "Handle unary minus." in prompt
    assert "- coding.systematic-debugging" in prompt


@pytest.mark.asyncio
async def test_original_task_instruction_is_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = FakeAgent()

    factory = AgentFactory([agent])
    executor = make_executor(monkeypatch, factory)

    task = make_task()

    state = make_state(
        attempt=3,
        progress="Half done.",
        remaining_work="Finish it.",
    )

    await executor.execute(task, state)

    # The Worker's user query is the original instruction, not the
    # accumulated progress.
    assert agent.queries == [INSTRUCTION]
    assert task.query == INSTRUCTION
    assert state.instruction == INSTRUCTION


# --------------------------------------------------------------------
# Restart suggestion
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_suggestion_reaches_new_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = {TASK_ID: "Check the changelog first."}

    def provider(task_id: str) -> str | None:
        return store.pop(task_id, None)

    factory = AgentFactory([FakeAgent()])
    executor = make_executor(
        monkeypatch,
        factory,
        restart_suggestion=provider,
    )

    await executor.execute(
        make_task(),
        make_state(attempt=2),
    )

    prompt = factory.calls[0]["system_prompt"]

    assert "## Supervisor Guidance" in prompt
    assert "Check the changelog first." in prompt


@pytest.mark.asyncio
async def test_restart_suggestion_is_consumed_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    store = {TASK_ID: "Prefer the upstream fix."}

    def provider(task_id: str) -> str | None:
        calls.append(task_id)
        return store.pop(task_id, None)

    factory = AgentFactory(
        [FakeAgent(), FakeAgent()]
    )

    executor = make_executor(
        monkeypatch,
        factory,
        restart_suggestion=provider,
    )

    task = make_task()
    state = make_state(attempt=2)

    await executor.execute(task, state)

    task.status = TaskStatus.QUEUED
    state.attempt = 3

    await executor.execute(task, state)

    assert calls == [TASK_ID, TASK_ID]

    assert (
        "Prefer the upstream fix."
        in factory.calls[0]["system_prompt"]
    )

    assert (
        "## Supervisor Guidance"
        not in factory.calls[1]["system_prompt"]
    )


@pytest.mark.asyncio
async def test_validation_failure_does_not_consume_suggestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumed: list[str] = []

    def provider(task_id: str) -> str | None:
        consumed.append(task_id)
        return "Guidance."

    factory = AgentFactory([FakeAgent()])

    executor = make_executor(
        monkeypatch,
        factory,
        restart_suggestion=provider,
    )

    with pytest.raises(ValueError, match="no authorized domains"):
        await executor.execute(
            make_task(domains=()),
            make_state(domains=(), attempt=2),
        )

    # Guidance is only consumed when an attempt is actually built.
    assert consumed == []


# --------------------------------------------------------------------
# Observability and legacy removal
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attempt_trace_exposes_runtime_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = RecordingTrace()

    factory = AgentFactory([FakeAgent()])

    executor = make_executor(
        monkeypatch,
        factory,
        trace=trace,
    )

    await executor.execute(
        make_task(domains=("coding", "research")),
        make_state(
            domains=("coding", "research"),
            attempt=2,
            loaded_skills=["coding.systematic-debugging"],
        ),
    )

    started = [
        event
        for event in trace.events
        if event["event"] == "attempt_started"
    ]

    assert len(started) == 1

    event = started[0]

    assert event["attempt"] == 2
    assert event["authorized_domains"] == [
        "coding",
        "research",
    ]
    assert event["execution_tools"] == [
        "shell",
        "web_search",
    ]
    assert event["control_tools"] == ["get_skill"]
    assert event["allowed_tools"] == [
        "get_skill",
        "shell",
        "web_search",
    ]
    assert event["loaded_skills"] == [
        "coding.systematic-debugging"
    ]

    # Domain or skill content is never traced here.
    assert "system_prompt" not in event


def test_task_executor_has_no_legacy_skill_architecture() -> None:
    source = EXECUTOR_PATH.read_text(encoding="utf-8")

    for removed in (
        "skills.registry",
        "from skills",
        "import skills",
        "skill_names",
        "skill.allowed_tools",
    ):
        assert removed not in source


# --------------------------------------------------------------------
# Structured Worker outcomes
# --------------------------------------------------------------------


class SkillUsingAgent(FakeAgent):
    """Worker double that calls get_skill before returning."""

    def __init__(
        self,
        skill_id: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(result)
        self.skill_id = skill_id
        self.skill_result: dict[str, Any] | None = None

    async def run(
        self,
        query: str,
        request_id: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        self.runs += 1
        self.queries.append(query)

        self.skill_result = json.loads(
            await self.tools.call_tool(
                "get_skill",
                {"skill_id": self.skill_id},
            )
        )

        if self.error is not None:
            raise self.error

        return self.result


def completed_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "completed",
        "result": {"content": "done"},
        "progress": "",
        "findings": [],
        "artifacts": [],
        "remaining_work": "",
        "escalation": None,
    }

    payload.update(overrides)

    return payload


def blocked_payload(
    escalation: dict[str, Any],
    **overrides: Any,
) -> dict[str, Any]:
    payload = completed_payload(
        status="blocked",
        result=None,
        progress="Partial work.",
        findings=["Found one defect."],
        artifacts=["trace.txt"],
        remaining_work="Finish the fix.",
        escalation=escalation,
    )

    payload.update(overrides)

    return payload


DOMAIN_ESCALATION: dict[str, Any] = {
    "kind": "domain_required",
    "reason": "Needs external sources.",
    "requested_domain": "research",
}


@pytest.mark.asyncio
async def test_completed_worker_result_becomes_completed_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory(
        [
            FakeAgent(
                completed_payload(
                    progress="All done.",
                    findings=["No defect remains."],
                    artifacts=["fix.diff"],
                )
            )
        ]
    )

    executor = make_executor(monkeypatch, factory)

    task = make_task()

    outcome = await executor.execute(task, make_state())

    assert outcome.status is TaskStatus.COMPLETED
    assert outcome.result == {"content": "done"}
    assert outcome.progress == "All done."
    assert outcome.findings == ("No defect remains.",)
    assert outcome.artifacts == ("fix.diff",)
    assert outcome.remaining_work == ""
    assert outcome.escalation is None

    assert task.status is TaskStatus.COMPLETED


@pytest.mark.parametrize(
    ("escalation", "kind", "field", "value"),
    [
        (
            {
                "kind": "domain_required",
                "reason": "Needs sources.",
                "requested_domain": "research",
            },
            EscalationKind.DOMAIN_REQUIRED,
            "requested_domain",
            "research",
        ),
        (
            {
                "kind": "tool_required",
                "reason": "Needs a shell.",
                "requested_tool": "shell",
            },
            EscalationKind.TOOL_REQUIRED,
            "requested_tool",
            "shell",
        ),
        (
            {
                "kind": "skill_limit_exceeded",
                "reason": "Needs another methodology.",
                "requested_skill_limit": 7,
            },
            EscalationKind.SKILL_LIMIT_EXCEEDED,
            "requested_skill_limit",
            7,
        ),
    ],
)
@pytest.mark.asyncio
async def test_blocked_worker_result_becomes_canonical_escalation(
    monkeypatch: pytest.MonkeyPatch,
    escalation: dict[str, Any],
    kind: EscalationKind,
    field: str,
    value: Any,
) -> None:
    factory = AgentFactory(
        [FakeAgent(blocked_payload(escalation))]
    )

    executor = make_executor(monkeypatch, factory)

    task = make_task()

    outcome = await executor.execute(task, make_state())

    assert outcome.status is TaskStatus.BLOCKED
    assert outcome.escalation is not None
    assert outcome.escalation.kind is kind
    assert getattr(outcome.escalation, field) == value

    assert task.status is TaskStatus.BLOCKED


@pytest.mark.asyncio
async def test_blocked_worker_result_preserves_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory(
        [FakeAgent(blocked_payload(DOMAIN_ESCALATION))]
    )

    executor = make_executor(monkeypatch, factory)

    outcome = await executor.execute(
        make_task(),
        make_state(),
    )

    assert outcome.progress == "Partial work."
    assert outcome.findings == ("Found one defect.",)
    assert outcome.artifacts == ("trace.txt",)
    assert outcome.remaining_work == "Finish the fix."


@pytest.mark.asyncio
async def test_completed_result_with_escalation_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory(
        [
            FakeAgent(
                completed_payload(
                    escalation=DOMAIN_ESCALATION
                )
            )
        ]
    )

    executor = make_executor(monkeypatch, factory)

    task = make_task()

    outcome = await executor.execute(task, make_state())

    assert outcome.status is TaskStatus.FAILED
    assert "must not carry an escalation" in (
        outcome.error or ""
    )
    assert task.status is TaskStatus.FAILED


@pytest.mark.asyncio
async def test_blocked_result_without_escalation_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory(
        [
            FakeAgent(
                completed_payload(
                    status="blocked",
                    escalation=None,
                )
            )
        ]
    )

    executor = make_executor(monkeypatch, factory)

    outcome = await executor.execute(
        make_task(),
        make_state(),
    )

    assert outcome.status is TaskStatus.FAILED
    assert "requires an escalation" in (outcome.error or "")


@pytest.mark.asyncio
async def test_unknown_escalation_kind_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory(
        [
            FakeAgent(
                blocked_payload(
                    {
                        "kind": "browser_required",
                        "reason": "Needs a browser.",
                    }
                )
            )
        ]
    )

    executor = make_executor(monkeypatch, factory)

    outcome = await executor.execute(
        make_task(),
        make_state(),
    )

    assert outcome.status is TaskStatus.FAILED
    assert "Unknown escalation kind" in (outcome.error or "")


@pytest.mark.asyncio
async def test_malformed_structured_output_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory(
        [FakeAgent({"content": "not json"})]
    )

    executor = make_executor(monkeypatch, factory)

    task = make_task()

    outcome = await executor.execute(task, make_state())

    assert outcome.status is TaskStatus.FAILED
    assert (outcome.error or "").startswith(
        "Invalid structured Worker result:"
    )
    assert task.status is TaskStatus.FAILED


@pytest.mark.asyncio
async def test_prose_is_never_treated_as_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory(
        [
            FakeAgent(
                {
                    "content": (
                        "I am blocked because I need "
                        "web_search."
                    )
                }
            )
        ]
    )

    executor = make_executor(monkeypatch, factory)

    outcome = await executor.execute(
        make_task(),
        make_state(),
    )

    assert outcome.status is TaskStatus.FAILED
    assert outcome.escalation is None


@pytest.mark.asyncio
async def test_fenced_json_result_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = completed_payload()

    factory = AgentFactory(
        [
            FakeAgent(
                {
                    "content": "```json\n"
                    + json.dumps(payload)
                    + "\n```",
                }
            )
        ]
    )

    executor = make_executor(monkeypatch, factory)

    outcome = await executor.execute(
        make_task(),
        make_state(),
    )

    assert outcome.status is TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_invalid_worker_result_is_traced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = RecordingTrace()

    factory = AgentFactory(
        [FakeAgent({"content": "{}"})]
    )

    executor = make_executor(
        monkeypatch,
        factory,
        trace=trace,
    )

    await executor.execute(make_task(), make_state())

    invalid = [
        event
        for event in trace.events
        if event["event"] == "worker_result_invalid"
    ]

    assert len(invalid) == 1
    assert invalid[0]["attempt"] == 1


# --------------------------------------------------------------------
# get_skill integration
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_receives_get_skill_as_control_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AgentFactory([FakeAgent()])

    executor = make_executor(monkeypatch, factory)

    await executor.execute(make_task(), make_state())

    call = factory.calls[0]

    # get_skill is control capability; execution tools stay
    # grant-derived only.
    assert call["allowed_tools"] == {
        "get_skill",
        "shell",
    }

    names = {
        tool["function"]["name"]
        for tool in await call["tools"].get_tools()
    }

    assert names == {
        "get_skill",
        "shell",
        "web_search",
        "sql_executor",
    }


@pytest.mark.asyncio
async def test_get_skill_updates_loaded_skills_during_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = SkillUsingAgent(
        "coding.systematic-debugging"
    )

    factory = AgentFactory([agent])

    executor = make_executor(monkeypatch, factory)

    state = make_state()

    outcome = await executor.execute(
        make_task(),
        state,
    )

    assert outcome.status is TaskStatus.COMPLETED
    assert state.loaded_skills == [
        "coding.systematic-debugging"
    ]

    assert agent.skill_result is not None
    assert agent.skill_result["ok"] is True
    assert (
        agent.skill_result["skill"]["name"]
        == "systematic-debugging"
    )

    # No authority is inferred from the loaded methodology.
    assert state.authorized_domains == {"coding"}


@pytest.mark.asyncio
async def test_unauthorized_skill_blocks_with_domain_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = SkillUsingAgent(
        "research.web-search",
        blocked_payload(DOMAIN_ESCALATION),
    )

    factory = AgentFactory([agent])

    executor = make_executor(monkeypatch, factory)

    state = make_state()

    outcome = await executor.execute(
        make_task(),
        state,
    )

    assert outcome.status is TaskStatus.BLOCKED
    assert outcome.escalation is not None
    assert (
        outcome.escalation.requested_domain == "research"
    )

    # The rejected load consumed no skill slot.
    assert state.loaded_skills == []

    assert agent.skill_result is not None
    assert (
        agent.skill_result["error"]
        == "unauthorized_skill"
    )


@pytest.mark.asyncio
async def test_blocked_outcome_is_accepted_by_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task -> DOMAIN -> get_skill -> BLOCKED -> Supervisor."""

    monkeypatch.setenv(
        "KERNELAI_VAULT_PATH",
        str(VAULT_PATH),
    )

    supervisor = Supervisor(
        request=Request(
            request_id="req-executor-e2e",
            query=INSTRUCTION,
        ),
        local=True,
    )

    delegated = await supervisor.control_client.call_tool(
        "delegate_task",
        {
            "task": INSTRUCTION,
            "domains": ["coding"],
        },
    )

    task_id = json.loads(delegated)["task_id"]
    task = supervisor.tasks[task_id]

    agent = SkillUsingAgent(
        "research.web-search",
        blocked_payload(DOMAIN_ESCALATION),
    )

    monkeypatch.setattr(
        "tasks.task_executor.create_agent",
        AgentFactory([agent]),
    )

    supervisor.task_executor.tool_client = FakeToolClient()

    outcomes = await supervisor._execute_batch([task])
    processed = supervisor._process_blocked_outcomes(outcomes)

    assert processed[0].status is TaskStatus.BLOCKED
    assert task.status is TaskStatus.BLOCKED

    resolved = supervisor.resolved_escalations[task_id]

    assert resolved.kind is EscalationKind.DOMAIN_REQUIRED
    assert resolved.candidate_domains == ("research",)
