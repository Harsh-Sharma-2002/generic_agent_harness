"""Tests for Supervisor-side Task escalation handling.

Escalations are decided by the Supervisor's normal reasoning step
through the ``resolve_escalation`` control tool. Nothing here makes a
model call: the deterministic batch processing is exercised directly,
and decisions are applied through the control client.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from supervisor import supervisor as supervisor_module
from supervisor.escalation import (
    MAX_ATTEMPTS,
    MAX_SKILL_LIMIT,
    EscalationValidationError,
    apply_decision,
    validate_escalation,
)
from supervisor.models import (
    MAX_SUGGESTION_LENGTH,
    EscalationDecision,
    EscalationDecisionKind,
)
from supervisor.supervisor import Supervisor
from tasks.models import (
    EscalationKind,
    EscalationRequest,
    TaskState,
)
from tasks.outcome import TaskOutcome
from tasks.request import Request
from tasks.task import Task, TaskStatus
from tool_grants.registry import TrustedDomainGrants


VAULT_PATH = Path(__file__).resolve().parents[1] / "vault"

DOMAINS = {"coding", "teaching", "research", "data"}


def domain_exists(domain: str) -> bool:
    """Stand-in for SecondBrainRuntime domain lookup."""

    return domain in DOMAINS


class FakeToolAuthority:
    """A TrustedDomainGrants-shaped resolver."""

    def __init__(
        self,
        grants: dict[str, set[str]] | None = None,
    ) -> None:
        self.grants = grants or {}

    def tools_for_domain(
        self,
        domain: str,
    ) -> frozenset[str]:
        return frozenset(
            self.grants.get(domain, set())
        )

    def domains_for_tool(
        self,
        tool_id: str,
    ) -> frozenset[str]:
        return frozenset(
            {
                domain
                for domain, tools in self.grants.items()
                if tool_id in tools
            }
        )


NO_TOOLS = FakeToolAuthority()


class FakeTaskExecutor:
    """Stand-in for the TaskExecutor, returning canned outcomes."""

    def __init__(
        self,
        outcomes: list[TaskOutcome],
    ) -> None:
        self.pending = list(outcomes)
        self.calls: list[tuple[str, TaskStatus]] = []

    async def execute(
        self,
        task: Task,
        task_state: TaskState,
    ) -> TaskOutcome:
        self.calls.append((task.task_id, task.status))
        return self.pending.pop(0)


class ExplodingLLM:
    """Fails the test if escalation handling touches the model."""

    def __init__(self) -> None:
        self.calls = 0

    async def call(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        self.calls += 1
        raise AssertionError(
            "Escalation processing must not call the model."
        )


def make_task_and_state(
    *,
    attempt: int = 1,
    skill_limit: int = 5,
    domains: tuple[str, ...] = ("coding",),
) -> tuple[Task, TaskState]:
    """Return a BLOCKED Task with accumulated work."""

    state = TaskState(
        task_id="task_escalation",
        instruction="Fix the parser.",
        attempt=attempt,
        authorized_domains=set(domains),
        skill_limit=skill_limit,
        loaded_skills=["coding.systematic-debugging"],
        progress="Located the parser.",
        findings=["The parser drops tokens."],
        artifacts=["diff.txt"],
        remaining_work="Run the parser tests.",
    )

    task = Task(
        request_id="req-escalation",
        task_id=state.task_id,
        query=state.instruction,
        authorized_domains=list(domains),
        status=TaskStatus.BLOCKED,
    )

    return task, state


def approve(
    suggestion: str | None = None,
) -> EscalationDecision:
    return EscalationDecision(
        decision=EscalationDecisionKind.APPROVE,
        suggestion=suggestion,
    )


def deny() -> EscalationDecision:
    return EscalationDecision(
        decision=EscalationDecisionKind.DENY,
    )


def terminate() -> EscalationDecision:
    return EscalationDecision(
        decision=EscalationDecisionKind.TERMINATE,
    )


def validate(
    state: TaskState,
    escalation: EscalationRequest | None,
    *,
    authority: Any = NO_TOOLS,
):
    return validate_escalation(
        state,
        escalation,
        domain_exists=domain_exists,
        tool_authority=authority,
    )


# --------------------------------------------------------------------
# BLOCKED without escalation
# --------------------------------------------------------------------


def test_blocked_without_escalation_is_rejected() -> None:
    """BLOCKED must carry a structured escalation."""

    _, state = make_task_and_state()

    with pytest.raises(
        EscalationValidationError,
        match="must carry an EscalationRequest",
    ):
        validate(state, None)


# --------------------------------------------------------------------
# DOMAIN_REQUIRED
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    "requested",
    [None, "", "   "],
)
def test_domain_required_requires_a_domain(
    requested: str | None,
) -> None:
    _, state = make_task_and_state()

    escalation = EscalationRequest(
        kind=EscalationKind.DOMAIN_REQUIRED,
        reason="Needs current documentation.",
        requested_domain=requested,
    )

    with pytest.raises(
        EscalationValidationError,
        match="requested_domain",
    ):
        validate(state, escalation)


def test_domain_required_rejects_authorized_domain() -> None:
    _, state = make_task_and_state(
        domains=("coding", "research"),
    )

    escalation = EscalationRequest(
        kind=EscalationKind.DOMAIN_REQUIRED,
        reason="Needs research.",
        requested_domain="research",
    )

    with pytest.raises(
        EscalationValidationError,
        match="already authorized",
    ):
        validate(state, escalation)


def test_domain_required_rejects_unknown_domain() -> None:
    _, state = make_task_and_state()

    escalation = EscalationRequest(
        kind=EscalationKind.DOMAIN_REQUIRED,
        reason="Needs something else.",
        requested_domain="astrology",
    )

    with pytest.raises(
        EscalationValidationError,
        match="Unknown domain",
    ):
        validate(state, escalation)


def test_domain_required_approval_adds_domain() -> None:
    task, state = make_task_and_state()

    escalation = EscalationRequest(
        kind=EscalationKind.DOMAIN_REQUIRED,
        reason="Needs current documentation.",
        requested_domain="research",
    )

    state.escalation = escalation

    resolved = validate(state, escalation)

    assert resolved.grantable is True
    assert resolved.candidate_domains == ("research",)

    resolution = apply_decision(
        task=task,
        state=state,
        decision=approve(),
        resolved=resolved,
    )

    assert resolution.restart is True
    assert state.authorized_domains == {"coding", "research"}

    # The Task keeps its originally delegated authority: current
    # execution authority lives on TaskState.
    assert task.authorized_domains == ["coding"]


# --------------------------------------------------------------------
# TOOL_REQUIRED
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    "requested",
    [None, "", "   "],
)
def test_tool_required_requires_a_tool(
    requested: str | None,
) -> None:
    _, state = make_task_and_state()

    escalation = EscalationRequest(
        kind=EscalationKind.TOOL_REQUIRED,
        reason="Needs a browser.",
        requested_tool=requested,
    )

    with pytest.raises(
        EscalationValidationError,
        match="requested_tool",
    ):
        validate(state, escalation)


def test_tool_required_rejects_already_granted_tool() -> None:
    authority = FakeToolAuthority(
        {"coding": {"shell"}}
    )

    _, state = make_task_and_state(
        domains=("coding",),
    )

    escalation = EscalationRequest(
        kind=EscalationKind.TOOL_REQUIRED,
        reason="Needs shell.",
        requested_tool="shell",
    )

    with pytest.raises(
        EscalationValidationError,
        match="already granted",
    ):
        validate(state, escalation, authority=authority)


def test_unknown_tool_cannot_manufacture_authority() -> None:
    """No trusted grant means approval cannot be honoured."""

    _, state = make_task_and_state()

    escalation = EscalationRequest(
        kind=EscalationKind.TOOL_REQUIRED,
        reason="Needs a browser.",
        requested_tool="browser",
    )

    resolved = validate(state, escalation)

    assert resolved.grantable is False
    assert resolved.candidate_domains == ()

    task = Task(
        request_id=state.task_id,
        task_id=state.task_id,
        query=state.instruction,
        authorized_domains=["coding"],
        status=TaskStatus.BLOCKED,
    )

    resolution = apply_decision(
        task=task,
        state=state,
        decision=approve(),
        resolved=resolved,
    )

    assert resolution.restart is False
    assert task.status == TaskStatus.FAILED
    assert state.authorized_domains == {"coding"}


def test_tool_approval_grants_domain_never_direct_tool() -> None:
    authority = FakeToolAuthority(
        {
            "coding": {"read_file"},
            "research": {"read_file", "web_search"},
        }
    )

    task, state = make_task_and_state(
        domains=("teaching",),
    )

    escalation = EscalationRequest(
        kind=EscalationKind.TOOL_REQUIRED,
        reason="Needs web search.",
        requested_tool="web_search",
    )

    resolved = validate(state, escalation, authority=authority)

    assert resolved.grantable is True
    assert resolved.candidate_domains == ("research",)

    resolution = apply_decision(
        task=task,
        state=state,
        decision=approve(),
        resolved=resolved,
    )

    assert resolution.restart is True
    assert resolution.granted_domain == "research"
    assert state.authorized_domains == {"teaching", "research"}

    # Tool authority stays derived; TaskState never holds tools.
    assert not hasattr(state, "allowed_tools")
    assert not hasattr(state, "approved_tools")
    assert not hasattr(state, "extra_tools")
    assert not hasattr(state, "tool_grants")


def test_tool_approval_grants_one_of_several_candidates() -> None:
    authority = FakeToolAuthority(
        {
            "coding": {"shell", "read_file", "write_file"},
            "data": {"shell"},
        }
    )

    task, state = make_task_and_state(
        domains=("teaching",),
    )

    escalation = EscalationRequest(
        kind=EscalationKind.TOOL_REQUIRED,
        reason="Needs shell.",
        requested_tool="shell",
    )

    resolved = validate(state, escalation, authority=authority)

    assert resolved.candidate_domains == ("coding", "data")

    resolution = apply_decision(
        task=task,
        state=state,
        decision=approve(),
        resolved=resolved,
        select_domain=lambda candidates, _state: min(
            candidates,
            key=lambda domain: (
                len(authority.tools_for_domain(domain)),
                domain,
            ),
        ),
    )

    # Only the narrowest granting domain is added.
    assert resolution.granted_domain == "data"
    assert state.authorized_domains == {"teaching", "data"}


# --------------------------------------------------------------------
# SKILL_LIMIT_EXCEEDED
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    "requested",
    [None, "7", True],
)
def test_skill_limit_requires_an_integer(
    requested: object,
) -> None:
    _, state = make_task_and_state()

    escalation = EscalationRequest(
        kind=EscalationKind.SKILL_LIMIT_EXCEEDED,
        reason="Needs more methodology.",
        requested_skill_limit=requested,
    )

    with pytest.raises(
        EscalationValidationError,
        match="requested_skill_limit",
    ):
        validate(state, escalation)


@pytest.mark.parametrize("requested", [5, 3])
def test_skill_limit_must_exceed_current(
    requested: int,
) -> None:
    _, state = make_task_and_state(
        skill_limit=5,
    )

    escalation = EscalationRequest(
        kind=EscalationKind.SKILL_LIMIT_EXCEEDED,
        reason="Needs more methodology.",
        requested_skill_limit=requested,
    )

    with pytest.raises(
        EscalationValidationError,
        match="greater than the current limit",
    ):
        validate(state, escalation)


def test_skill_limit_approval_increments_by_two() -> None:
    task, state = make_task_and_state(
        skill_limit=5,
    )

    escalation = EscalationRequest(
        kind=EscalationKind.SKILL_LIMIT_EXCEEDED,
        reason="Needs more methodology.",
        requested_skill_limit=20,
    )

    resolved = validate(state, escalation)

    resolution = apply_decision(
        task=task,
        state=state,
        decision=approve(),
        resolved=resolved,
    )

    # Runtime owns the amount; the requested 20 is informational.
    assert resolution.restart is True
    assert state.skill_limit == 7


def test_skill_limit_stops_at_hard_maximum() -> None:
    task, state = make_task_and_state(
        skill_limit=9,
    )

    escalation = EscalationRequest(
        kind=EscalationKind.SKILL_LIMIT_EXCEEDED,
        reason="Needs more methodology.",
        requested_skill_limit=11,
    )

    resolved = validate(state, escalation)

    apply_decision(
        task=task,
        state=state,
        decision=approve(),
        resolved=resolved,
    )

    assert state.skill_limit == MAX_SKILL_LIMIT == 11

    # Nothing is left to grant at the ceiling.
    at_max = validate(
        state,
        EscalationRequest(
            kind=EscalationKind.SKILL_LIMIT_EXCEEDED,
            reason="Needs even more.",
            requested_skill_limit=13,
        ),
    )

    assert at_max.grantable is False


# --------------------------------------------------------------------
# Decision application
# --------------------------------------------------------------------


def test_approve_increments_attempt_and_clears_escalation() -> None:
    task, state = make_task_and_state()

    escalation = EscalationRequest(
        kind=EscalationKind.DOMAIN_REQUIRED,
        reason="Needs research.",
        requested_domain="research",
    )

    state.escalation = escalation
    task_id = state.task_id

    resolution = apply_decision(
        task=task,
        state=state,
        decision=approve("Check the upstream changelog first."),
        resolved=validate(state, escalation),
    )

    assert resolution.restart is True
    assert state.attempt == 2
    assert state.escalation is None
    assert task.status == TaskStatus.QUEUED

    # Same semantic Task and same persistent state.
    assert task.task_id == state.task_id == task_id
    assert task.request_id == "req-escalation"

    # Accumulated work survives.
    assert state.progress == "Located the parser."
    assert state.findings == ["The parser drops tokens."]
    assert state.artifacts == ["diff.txt"]
    assert state.remaining_work == "Run the parser tests."
    assert state.loaded_skills == ["coding.systematic-debugging"]


def test_deny_retries_without_expanding_authority() -> None:
    task, state = make_task_and_state()

    escalation = EscalationRequest(
        kind=EscalationKind.DOMAIN_REQUIRED,
        reason="Needs research.",
        requested_domain="research",
    )

    state.escalation = escalation

    resolution = apply_decision(
        task=task,
        state=state,
        decision=deny(),
        resolved=validate(state, escalation),
    )

    assert resolution.restart is True
    assert state.attempt == 2
    assert state.authorized_domains == {"coding"}
    assert state.escalation is None
    assert task.status == TaskStatus.QUEUED


def test_terminate_does_not_restart_and_preserves_work() -> None:
    task, state = make_task_and_state()

    escalation = EscalationRequest(
        kind=EscalationKind.DOMAIN_REQUIRED,
        reason="Needs research.",
        requested_domain="research",
    )

    state.escalation = escalation

    resolution = apply_decision(
        task=task,
        state=state,
        decision=terminate(),
        resolved=validate(state, escalation),
    )

    assert resolution.restart is False
    assert task.status == TaskStatus.FAILED
    assert state.attempt == 1
    assert state.authorized_domains == {"coding"}

    # Partial work is kept for Request-level synthesis.
    assert state.progress == "Located the parser."
    assert state.findings == ["The parser drops tokens."]
    assert state.artifacts == ["diff.txt"]
    assert state.remaining_work == "Run the parser tests."
    assert state.loaded_skills == ["coding.systematic-debugging"]


@pytest.mark.parametrize(
    "decision",
    [
        pytest.param(approve, id="approve"),
        pytest.param(deny, id="deny"),
    ],
)
def test_final_attempt_cannot_restart(
    decision: Any,
) -> None:
    task, state = make_task_and_state(
        attempt=MAX_ATTEMPTS,
    )

    escalation = EscalationRequest(
        kind=EscalationKind.DOMAIN_REQUIRED,
        reason="Needs research.",
        requested_domain="research",
    )

    state.escalation = escalation

    resolution = apply_decision(
        task=task,
        state=state,
        decision=decision(),
        resolved=validate(state, escalation),
    )

    assert resolution.restart is False
    assert task.status == TaskStatus.FAILED
    assert state.attempt == MAX_ATTEMPTS

    # No authority is granted for an attempt that will not run.
    assert state.authorized_domains == {"coding"}


def test_suggestion_longer_than_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="at most"):
        EscalationDecision(
            decision=EscalationDecisionKind.APPROVE,
            suggestion="x" * (MAX_SUGGESTION_LENGTH + 1),
        )

    # The boundary itself is accepted.
    decision = EscalationDecision(
        decision=EscalationDecisionKind.DENY,
        suggestion="y" * MAX_SUGGESTION_LENGTH,
    )

    assert len(decision.suggestion or "") == MAX_SUGGESTION_LENGTH


# --------------------------------------------------------------------
# Supervisor wiring
# --------------------------------------------------------------------


def build_supervisor(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tool_authority: Any = None,
) -> Supervisor:
    monkeypatch.setenv(
        "KERNELAI_VAULT_PATH",
        str(VAULT_PATH),
    )

    return Supervisor(
        request=Request(
            request_id="req-escalation",
            query="Fix the parser.",
        ),
        local=True,
        tool_authority=tool_authority,
    )


async def delegate(
    supervisor: Supervisor,
    *,
    domains: tuple[str, ...] = ("coding",),
) -> tuple[Task, TaskState]:
    """Delegate one Task through the control tool."""

    before = set(supervisor.tasks)

    await supervisor.control_client.call_tool(
        "delegate_task",
        {
            "task": "Fix the parser.",
            "domains": list(domains),
        },
    )

    (task_id,) = set(supervisor.tasks) - before

    task = supervisor.tasks[task_id]
    state = supervisor.task_states[task.task_id]

    state.loaded_skills = ["coding.systematic-debugging"]

    return task, state


def blocked_outcome(
    task: Task,
    *,
    escalation: EscalationRequest | None,
) -> TaskOutcome:
    return TaskOutcome(
        request_id=task.request_id,
        task_id=task.task_id,
        status=TaskStatus.BLOCKED,
        progress="Parsed the grammar, failed on unary minus.",
        findings=("Unary minus is not handled.",),
        artifacts=("trace.txt",),
        remaining_work="Handle unary minus.",
        escalation=escalation,
    )


async def blocked_task(
    supervisor: Supervisor,
    *,
    escalation: EscalationRequest | None,
    domains: tuple[str, ...] = ("coding",),
    attempt: int = 1,
) -> tuple[Task, TaskState]:
    """Produce a Task that is BLOCKED and awaiting a decision."""

    task, state = await delegate(
        supervisor,
        domains=domains,
    )

    state.attempt = attempt

    # The Supervisor loop drains the runnable batch before executing
    # it, so a Task that just came back BLOCKED is no longer pending.
    supervisor.pending_tasks = []

    outcome = blocked_outcome(
        task,
        escalation=escalation,
    )

    # As the batch would have recorded it before processing.
    supervisor.outcomes[task.task_id] = outcome

    processed = supervisor._process_blocked_outcomes(
        [outcome]
    )

    # The stored outcome is the processed one.
    assert supervisor.outcomes[task.task_id] is processed[0]

    return task, state


async def resolve(
    supervisor: Supervisor,
    task_id: str,
    decision: str,
    suggestion: str | None = None,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "task_id": task_id,
        "decision": decision,
    }

    if suggestion is not None:
        arguments["suggestion"] = suggestion

    return await supervisor.control_client.call_tool(
        "resolve_escalation",
        arguments,
    )


def test_supervisor_exposes_two_control_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    assert set(supervisor.control_client.tools) == {
        "delegate_task",
        "resolve_escalation",
    }

    assert supervisor.agent.allowed_tools == {
        "delegate_task",
        "resolve_escalation",
    }


def test_no_special_escalation_inference_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escalation is decided by the normal Supervisor step only."""

    supervisor = build_supervisor(monkeypatch)

    assert not hasattr(supervisor, "escalation_decider")
    assert not hasattr(supervisor, "_decide_escalation")
    assert not hasattr(supervisor, "_decide_escalation_via_model")
    assert not hasattr(supervisor, "_run_batch_with_escalation")

    source = Path(
        supervisor_module.__file__
    ).read_text(encoding="utf-8")

    for removed in (
        "ESCALATION_DECISION_PROMPT",

        "_parse_escalation_decision",
        "EscalationDecider",
    ):
        assert removed not in source

    # Outcome processing is deterministic and synchronous.
    assert not inspect.iscoroutinefunction(
        Supervisor._process_blocked_outcomes
    )


@pytest.mark.asyncio
async def test_no_model_call_for_any_number_of_blocked_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0, 1, or several blocked Tasks all cost zero extra calls."""

    supervisor = build_supervisor(monkeypatch)

    exploding = ExplodingLLM()
    supervisor.agent.llm = exploding

    # Zero blocked Tasks.
    assert supervisor._process_blocked_outcomes([]) == []

    one, _ = await delegate(supervisor)

    supervisor.outcomes[one.task_id] = blocked_outcome(
        one,
        escalation=EscalationRequest(
            kind=EscalationKind.DOMAIN_REQUIRED,
            reason="Needs current docs.",
            requested_domain="research",
        ),
    )

    supervisor._process_blocked_outcomes(
        [supervisor.outcomes[one.task_id]]
    )

    # Several blocked Tasks in one batch.
    batch: list[Task] = []

    for _ in range(3):
        another, _state = await delegate(supervisor)
        batch.append(another)

    outcomes = []

    for another in batch:
        outcome = blocked_outcome(
            another,
            escalation=EscalationRequest(
                kind=EscalationKind.DOMAIN_REQUIRED,
                reason="Needs current docs.",
                requested_domain="research",
            ),
        )

        supervisor.outcomes[
            another.task_id
        ] = outcome

        outcomes.append(outcome)

    processed = supervisor._process_blocked_outcomes(
        outcomes
    )

    assert len(processed) == 3
    assert len(supervisor.resolved_escalations) == 4

    # All blocked Tasks are visible to the next step together.
    supervisor._add_outcomes_to_context(processed)

    content = supervisor.agent.messages[-1]["content"]

    for another in batch:
        assert another.task_id in content

    assert content.count('"status": "blocked"') == 3

    # No extra inference for any of them.
    assert exploding.calls == 0


@pytest.mark.asyncio
async def test_batch_executes_concurrently_and_records_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    first, _ = await delegate(supervisor)
    second, _ = await delegate(supervisor)

    completed = TaskOutcome(
        request_id=first.request_id,
        task_id=first.task_id,
        status=TaskStatus.COMPLETED,
        result={"ok": True},
    )

    blocked = blocked_outcome(
        second,
        escalation=EscalationRequest(
            kind=EscalationKind.DOMAIN_REQUIRED,
            reason="Needs current docs.",
            requested_domain="research",
        ),
    )

    supervisor.task_executor = FakeTaskExecutor(
        [completed, blocked]
    )

    outcomes = await supervisor._execute_batch(
        [first, second]
    )

    assert [outcome.task_id for outcome in outcomes] == [
        first.task_id,
        second.task_id,
    ]

    assert supervisor.outcomes[first.task_id] is completed
    assert supervisor.outcomes[second.task_id] is blocked

    # Both ran from the same batch, in queue order.
    assert supervisor.task_executor.calls == [
        (first.task_id, TaskStatus.QUEUED),
        (second.task_id, TaskStatus.QUEUED),
    ]


@pytest.mark.asyncio
async def test_blocked_outcome_is_persisted_and_awaits_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    task, state = await blocked_task(
        supervisor,
        escalation=EscalationRequest(
            kind=EscalationKind.DOMAIN_REQUIRED,
            reason="Needs current docs.",
            requested_domain="research",
        ),
    )

    # Deterministic processing persisted Worker work and left the
    # Task resumable.
    assert task.status == TaskStatus.BLOCKED
    assert state.progress.startswith("Parsed the grammar")
    assert state.findings == ["Unary minus is not handled."]
    assert state.artifacts == ["trace.txt"]
    assert state.remaining_work == "Handle unary minus."

    # Control state was not touched by Worker output.
    assert state.attempt == 1
    assert state.authorized_domains == {"coding"}
    assert state.skill_limit == 5
    assert state.loaded_skills == ["coding.systematic-debugging"]

    # A validated escalation is available to the next step.
    assert task.task_id in supervisor.resolved_escalations
    assert state.escalation is not None

    # Nothing was requeued: only the next Supervisor step decides.
    assert supervisor.pending_tasks == []


@pytest.mark.asyncio
async def test_invalid_escalation_terminates_without_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    exploding = ExplodingLLM()
    supervisor.agent.llm = exploding

    task, state = await blocked_task(
        supervisor,
        escalation=None,
    )

    assert exploding.calls == 0
    assert task.status == TaskStatus.FAILED

    outcome = supervisor.outcomes[task.task_id]

    assert outcome.status == TaskStatus.FAILED
    assert "must carry an EscalationRequest" in (outcome.error or "")

    # Partial work survives termination.
    assert state.progress.startswith("Parsed the grammar")
    assert task.task_id not in supervisor.resolved_escalations


@pytest.mark.asyncio
async def test_context_contains_every_batch_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    completed_task, _ = await delegate(supervisor)
    blocked_task_obj, blocked_state = await delegate(supervisor)

    completed = TaskOutcome(
        request_id=completed_task.request_id,
        task_id=completed_task.task_id,
        status=TaskStatus.COMPLETED,
        result={"answer": 42},
    )

    blocked = blocked_outcome(
        blocked_task_obj,
        escalation=EscalationRequest(
            kind=EscalationKind.TOOL_REQUIRED,
            reason="Needs web search.",
            requested_tool="web_search",
        ),
    )

    supervisor.task_executor = FakeTaskExecutor(
        [completed, blocked]
    )

    outcomes = await supervisor._execute_batch(
        [completed_task, blocked_task_obj]
    )

    processed = supervisor._process_blocked_outcomes(
        outcomes
    )

    supervisor._add_outcomes_to_context(processed)

    message = supervisor.agent.messages[-1]
    payload = json.loads(
        message["content"].split("\n\n", 1)[1]
    )

    by_id = {
        entry["task_id"]: entry
        for entry in payload
    }

    assert by_id[completed_task.task_id]["status"] == "completed"
    assert by_id[completed_task.task_id]["result"] == {"answer": 42}

    blocked_entry = by_id[blocked_task_obj.task_id]

    assert blocked_entry["status"] == "blocked"
    assert blocked_entry["attempt"] == 1
    assert blocked_entry["instruction"] == "Fix the parser."
    assert blocked_entry["authorized_domains"] == ["coding"]
    assert blocked_entry["skill_limit"] == 5
    assert blocked_entry["progress"].startswith("Parsed the grammar")
    assert blocked_entry["findings"] == ["Unary minus is not handled."]
    assert blocked_entry["artifacts"] == ["trace.txt"]
    assert blocked_entry["remaining_work"] == "Handle unary minus."
    assert blocked_entry["escalation"] == {
        "kind": "tool_required",
        "reason": "Needs web search.",
        "requested_domain": None,
        "requested_tool": "web_search",
        "requested_skill_limit": None,
    }

    # Trusted grants expose the domains able to satisfy the tool.
    assert blocked_entry["candidate_domains"] == [
        "research",
        "teaching",
    ]

    # No Worker conversation or trace leaks upward.
    assert "messages" not in json.dumps(blocked_entry)
    assert blocked_state.progress.startswith("Parsed the grammar")


@pytest.mark.asyncio
async def test_approve_requeues_same_task_with_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    task, state = await blocked_task(
        supervisor,
        escalation=EscalationRequest(
            kind=EscalationKind.DOMAIN_REQUIRED,
            reason="Needs current docs.",
            requested_domain="research",
        ),
    )

    task_id = task.task_id

    await resolve(
        supervisor,
        task_id,
        "approve",
    )

    assert task.status == TaskStatus.QUEUED
    assert state.attempt == 2
    assert state.authorized_domains == {"coding", "research"}
    assert state.escalation is None
    assert supervisor.resolved_escalations == {}

    # The same Task object is in this step's runnable batch.
    assert supervisor.pending_tasks == [task]
    assert task.task_id == task_id
    assert supervisor.task_states[task_id] is state

    # Worker progress survived.
    assert state.progress.startswith("Parsed the grammar")


@pytest.mark.asyncio
async def test_deny_requeues_without_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    task, state = await blocked_task(
        supervisor,
        escalation=EscalationRequest(
            kind=EscalationKind.SKILL_LIMIT_EXCEEDED,
            reason="Needs a third methodology.",
            requested_skill_limit=9,
        ),
    )

    await resolve(
        supervisor,
        task.task_id,
        "deny",
    )

    assert task.status == TaskStatus.QUEUED
    assert state.attempt == 2
    assert state.skill_limit == 5
    assert state.authorized_domains == {"coding"}
    assert state.escalation is None
    assert supervisor.pending_tasks == [task]


@pytest.mark.asyncio
async def test_terminate_does_not_requeue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    task, state = await blocked_task(
        supervisor,
        escalation=EscalationRequest(
            kind=EscalationKind.DOMAIN_REQUIRED,
            reason="Needs current docs.",
            requested_domain="research",
        ),
    )

    await resolve(
        supervisor,
        task.task_id,
        "terminate",
    )

    assert task.status == TaskStatus.FAILED
    assert supervisor.pending_tasks == []
    assert state.attempt == 1

    # Partial work and authority state are preserved.
    assert state.progress.startswith("Parsed the grammar")
    assert state.findings == ["Unary minus is not handled."]
    assert state.loaded_skills == ["coding.systematic-debugging"]
    assert state.authorized_domains == {"coding"}


@pytest.mark.asyncio
async def test_tool_escalation_grants_minimum_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(
        monkeypatch,
        tool_authority=FakeToolAuthority(
            {
                "coding": {"shell", "read_file", "write_file"},
                "data": {"shell"},
            }
        ),
    )

    task, state = await blocked_task(
        supervisor,
        domains=("teaching",),
        escalation=EscalationRequest(
            kind=EscalationKind.TOOL_REQUIRED,
            reason="Needs shell access.",
            requested_tool="shell",
        ),
    )

    assert supervisor.resolved_escalations[
        task.task_id
    ].candidate_domains == ("coding", "data")

    await resolve(
        supervisor,
        task.task_id,
        "approve",
    )

    assert task.status == TaskStatus.QUEUED
    assert state.authorized_domains == {"teaching", "data"}
    assert not hasattr(state, "allowed_tools")


@pytest.mark.asyncio
async def test_unknown_tool_cannot_manufacture_trusted_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    assert isinstance(
        supervisor.tool_authority,
        TrustedDomainGrants,
    )

    task, state = await blocked_task(
        supervisor,
        escalation=EscalationRequest(
            kind=EscalationKind.TOOL_REQUIRED,
            reason="Needs a browser.",
            requested_tool="browser",
        ),
    )

    await resolve(
        supervisor,
        task.task_id,
        "approve",
    )

    # Approval cannot manufacture authority.
    assert task.status == TaskStatus.FAILED
    assert supervisor.pending_tasks == []
    assert state.authorized_domains == {"coding"}


@pytest.mark.asyncio
async def test_skill_limit_approval_is_plus_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    task, state = await blocked_task(
        supervisor,
        escalation=EscalationRequest(
            kind=EscalationKind.SKILL_LIMIT_EXCEEDED,
            reason="Needs more methodology.",
            requested_skill_limit=11,
        ),
    )

    await resolve(
        supervisor,
        task.task_id,
        "approve",
    )

    # Runtime owns the amount; 11 was requested, 7 was granted.
    assert state.skill_limit == 7
    assert task.status == TaskStatus.QUEUED


@pytest.mark.asyncio
async def test_final_attempt_cannot_restart_via_control_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    task, state = await blocked_task(
        supervisor,
        attempt=MAX_ATTEMPTS,
        escalation=EscalationRequest(
            kind=EscalationKind.DOMAIN_REQUIRED,
            reason="Needs current docs.",
            requested_domain="research",
        ),
    )

    await resolve(
        supervisor,
        task.task_id,
        "approve",
    )

    assert task.status == TaskStatus.FAILED
    assert supervisor.pending_tasks == []
    assert state.attempt == MAX_ATTEMPTS
    assert state.authorized_domains == {"coding"}


@pytest.mark.asyncio
async def test_resolve_requires_a_blocked_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    task, _ = await delegate(supervisor)

    assert task.status == TaskStatus.QUEUED

    with pytest.raises(ValueError, match="not BLOCKED"):
        await resolve(
            supervisor,
            task.task_id,
            "approve",
        )


@pytest.mark.asyncio
async def test_resolve_requires_a_validated_escalation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    task, state = await delegate(supervisor)

    task.status = TaskStatus.BLOCKED

    with pytest.raises(ValueError, match="no active escalation"):
        await resolve(
            supervisor,
            task.task_id,
            "approve",
        )

    # An escalation that was never validated is not actionable.
    state.escalation = EscalationRequest(
        kind=EscalationKind.DOMAIN_REQUIRED,
        reason="Needs research.",
        requested_domain="research",
    )

    with pytest.raises(ValueError, match="no validated escalation"):
        await resolve(
            supervisor,
            task.task_id,
            "approve",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decision",
    ["", "APPROVE_ISH", "approved", 1, None],
)
async def test_resolve_rejects_unknown_decision(
    monkeypatch: pytest.MonkeyPatch,
    decision: Any,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    task, state = await blocked_task(
        supervisor,
        escalation=EscalationRequest(
            kind=EscalationKind.DOMAIN_REQUIRED,
            reason="Needs current docs.",
            requested_domain="research",
        ),
    )

    with pytest.raises(ValueError, match="decision"):
        await resolve(
            supervisor,
            task.task_id,
            decision,
        )

    # Nothing was applied.
    assert task.status == TaskStatus.BLOCKED
    assert state.attempt == 1
    assert supervisor.pending_tasks == []


@pytest.mark.asyncio
async def test_resolve_rejects_unknown_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    with pytest.raises(ValueError, match="Unknown task_id"):
        await resolve(
            supervisor,
            "task_missing",
            "approve",
        )


@pytest.mark.asyncio
async def test_resolve_rejects_overlong_suggestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    task, state = await blocked_task(
        supervisor,
        escalation=EscalationRequest(
            kind=EscalationKind.DOMAIN_REQUIRED,
            reason="Needs current docs.",
            requested_domain="research",
        ),
    )

    with pytest.raises(ValueError, match="at most"):
        await resolve(
            supervisor,
            task.task_id,
            "approve",
            suggestion="x" * (MAX_SUGGESTION_LENGTH + 1),
        )

    # Rejected, not truncated: nothing was applied.
    assert task.status == TaskStatus.BLOCKED
    assert state.attempt == 1
    assert supervisor.pending_tasks == []


@pytest.mark.asyncio
async def test_suggestion_is_attempt_local_not_task_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    task, state = await blocked_task(
        supervisor,
        escalation=EscalationRequest(
            kind=EscalationKind.DOMAIN_REQUIRED,
            reason="Needs current docs.",
            requested_domain="research",
        ),
    )

    await resolve(
        supervisor,
        task.task_id,
        "approve",
        suggestion="Check the changelog before editing.",
    )

    # Not part of persistent TaskState.
    assert "suggestion" not in {
        field.name for field in fields(TaskState)
    }

    # Available once to whoever starts the next attempt.
    assert supervisor.take_restart_suggestion(
        task.task_id
    ) == "Check the changelog before editing."

    assert supervisor.take_restart_suggestion(
        task.task_id
    ) is None


@pytest.mark.asyncio
async def test_restarted_and_new_tasks_share_next_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    blocked, blocked_state = await blocked_task(
        supervisor,
        escalation=EscalationRequest(
            kind=EscalationKind.DOMAIN_REQUIRED,
            reason="Needs current docs.",
            requested_domain="research",
        ),
    )

    await resolve(
        supervisor,
        blocked.task_id,
        "approve",
    )

    new_task, _ = await delegate(supervisor)

    # Both are runnable in the batch produced by this step.
    assert supervisor.pending_tasks == [blocked, new_task]

    restart_outcome = TaskOutcome(
        request_id=blocked.request_id,
        task_id=blocked.task_id,
        status=TaskStatus.COMPLETED,
        result={"fixed": True},
    )

    new_outcome = TaskOutcome(
        request_id=new_task.request_id,
        task_id=new_task.task_id,
        status=TaskStatus.COMPLETED,
        result={"answer": 7},
    )

    executor = FakeTaskExecutor(
        [restart_outcome, new_outcome]
    )

    supervisor.task_executor = executor

    outcomes = await supervisor._execute_batch(
        [blocked, new_task]
    )

    assert [outcome.task_id for outcome in outcomes] == [
        blocked.task_id,
        new_task.task_id,
    ]

    assert blocked_state.attempt == 2
    assert blocked.status == TaskStatus.COMPLETED


# --------------------------------------------------------------------
# Trusted Domain Grants wiring
# --------------------------------------------------------------------


def test_supervisor_defaults_to_trusted_grants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production default is the trusted registry itself."""

    supervisor = build_supervisor(monkeypatch)

    assert isinstance(
        supervisor.tool_authority,
        TrustedDomainGrants,
    )

    assert supervisor.tool_authority.tools_for_domain(
        "coding"
    ) == frozenset({"shell"})

    assert supervisor.tool_authority.domains_for_tool(
        "web_search"
    ) == frozenset({"research", "teaching"})


@pytest.mark.asyncio
async def test_tool_required_rejects_tool_already_granted_by_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool reachable through current domains is not an escalation."""

    supervisor = build_supervisor(monkeypatch)

    task, state = await blocked_task(
        supervisor,
        domains=("research",),
        escalation=EscalationRequest(
            kind=EscalationKind.TOOL_REQUIRED,
            reason="Needs web search.",
            requested_tool="web_search",
        ),
    )

    # Rejected deterministically, without granting another domain.
    assert task.status == TaskStatus.FAILED
    assert task.task_id not in supervisor.resolved_escalations

    error = supervisor.outcomes[task.task_id].error or ""

    assert "already granted" in error
    assert state.authorized_domains == {"research"}


@pytest.mark.asyncio
async def test_tool_required_exposes_trusted_candidate_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Supervisor sees real candidates during its normal step."""

    supervisor = build_supervisor(monkeypatch)

    task, state = await blocked_task(
        supervisor,
        domains=("coding",),
        escalation=EscalationRequest(
            kind=EscalationKind.TOOL_REQUIRED,
            reason="Needs web search.",
            requested_tool="web_search",
        ),
    )

    resolved = supervisor.resolved_escalations[
        task.task_id
    ]

    assert resolved.grantable is True
    assert resolved.requested_tool == "web_search"
    assert resolved.candidate_domains == (
        "research",
        "teaching",
    )

    # The next step's context carries them.
    supervisor._add_outcomes_to_context(
        [supervisor.outcomes[task.task_id]]
    )

    payload = json.loads(
        supervisor.agent.messages[-1][
            "content"
        ].split("\n\n", 1)[1]
    )

    assert payload[0]["candidate_domains"] == [
        "research",
        "teaching",
    ]

    await resolve(
        supervisor,
        task.task_id,
        "approve",
    )

    # Exactly one minimum domain is added — never the tool itself.
    assert resolution_domains(supervisor, state) == {
        "coding",
        "research",
    }
    assert "teaching" not in state.authorized_domains
    assert not hasattr(state, "allowed_tools")


@pytest.mark.asyncio
async def test_approved_tool_is_derived_not_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authority grows by domain; tools are recomputed."""

    supervisor = build_supervisor(monkeypatch)

    task, state = await blocked_task(
        supervisor,
        domains=("coding",),
        escalation=EscalationRequest(
            kind=EscalationKind.TOOL_REQUIRED,
            reason="Needs web search.",
            requested_tool="web_search",
        ),
    )

    await resolve(
        supervisor,
        task.task_id,
        "approve",
    )

    derived = set()

    for domain in state.authorized_domains:
        derived |= supervisor.tool_authority.tools_for_domain(
            domain
        )

    assert derived == {"shell", "web_search"}
    assert "web_search" not in state.authorized_domains


def test_task_state_still_has_no_tool_authority_field() -> None:
    """Tool authority is always derived, never persisted."""

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


def resolution_domains(
    supervisor: Supervisor,
    state: TaskState,
) -> set[str]:
    """Return the Task's current authorized domains."""

    assert state.task_id in supervisor.task_states
    return set(state.authorized_domains)
