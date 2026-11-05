"""TaskExecutor attempt-failure conversion.

Every Task-runtime failure becomes a FAILED TaskOutcome, so a single
Task cannot abort its siblings or the Request. Cancellation propagates.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from second_brain.models import (
    SecondBrainError,
    SecondBrainErrorCode,
)
from second_brain.runtime import SecondBrainRuntime
from tasks.models import TaskState
from tasks.task import Task, TaskStatus
from tasks.task_executor import TaskExecutor
from tool_grants.registry import TrustedDomainGrants
from worker.message import (
    WorkerIterationExhausted,
    WorkerTaskResult,
)


VAULT_PATH = (
    Path(__file__).resolve().parents[1] / "vault"
)

TASK_ID = "task_failure"

INSTRUCTION = "Fix the parser."


class FakeToolClient:
    def __init__(
        self,
        names: set[str] | None = None,
        discovery_error: Exception | None = None,
    ) -> None:
        self.names = (
            {"shell"}
            if names is None
            else set(names)
        )
        self.discovery_error = discovery_error

    async def get_tools(
        self,
    ) -> list[dict[str, Any]]:
        if self.discovery_error is not None:
            raise self.discovery_error

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
    def __init__(
        self,
        result: WorkerTaskResult | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.result = (
            WorkerTaskResult(
                status="completed",
                result={"content": "done"},
            )
            if result is None
            else result
        )
        self.error = error

    async def run(
        self,
        query: str,
        request_id: str,
        task_id: str | None = None,
    ) -> WorkerTaskResult:
        if self.error is not None:
            raise self.error

        return self.result


class AgentFactory:
    def __init__(
        self,
        agent: FakeAgent | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.agent = agent or FakeAgent()
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        **kwargs: Any,
    ) -> FakeAgent:
        self.calls.append(kwargs)

        if self.error is not None:
            raise self.error

        return self.agent


class BrokenSecondBrain(SecondBrainRuntime):
    def get_domain(
        self,
        domain: str,
    ) -> str | SecondBrainError:
        return SecondBrainError(
            code=SecondBrainErrorCode.DOMAIN_NOT_FOUND,
            message=f"Domain {domain!r} is missing.",
        )


class RecordingTrace:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(
        self,
        **kwargs: Any,
    ) -> None:
        self.events.append(kwargs)


def make_executor(
    monkeypatch: pytest.MonkeyPatch,
    factory: AgentFactory,
    *,
    tool_client: FakeToolClient | None = None,
    trace: RecordingTrace | None = None,
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
        local=True,
    )


def make_task(
    status: TaskStatus = TaskStatus.QUEUED,
) -> Task:
    return Task(
        request_id="req-failure",
        task_id=TASK_ID,
        query=INSTRUCTION,
        authorized_domains=["coding"],
        status=status,
    )


def make_state(
    **kwargs: Any,
) -> TaskState:
    base: dict[str, Any] = {
        "task_id": TASK_ID,
        "instruction": INSTRUCTION,
        "authorized_domains": {"coding"},
    }

    base.update(kwargs)

    return TaskState(**base)


@pytest.mark.asyncio
async def test_domain_document_failure_becomes_failed_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = make_executor(
        monkeypatch,
        AgentFactory(),
        second_brain=BrokenSecondBrain(
            VAULT_PATH
        ),
    )

    task = make_task()

    outcome = await executor.execute(
        task,
        make_state(),
    )

    assert outcome.status is TaskStatus.FAILED
    assert "could not be loaded" in (
        outcome.error or ""
    )
    assert task.status is TaskStatus.FAILED


@pytest.mark.asyncio
async def test_tool_discovery_failure_becomes_failed_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = make_executor(
        monkeypatch,
        AgentFactory(),
        tool_client=FakeToolClient(
            discovery_error=ConnectionError(
                "MCP unavailable"
            )
        ),
    )

    task = make_task()

    outcome = await executor.execute(
        task,
        make_state(),
    )

    assert outcome.status is TaskStatus.FAILED
    assert "MCP unavailable" in (
        outcome.error or ""
    )
    assert task.status is TaskStatus.FAILED


@pytest.mark.asyncio
async def test_worker_construction_failure_becomes_failed_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = make_executor(
        monkeypatch,
        AgentFactory(
            error=RuntimeError("factory exploded")
        ),
    )

    task = make_task()

    outcome = await executor.execute(
        task,
        make_state(),
    )

    assert outcome.status is TaskStatus.FAILED
    assert outcome.error == "factory exploded"
    assert task.status is TaskStatus.FAILED


@pytest.mark.asyncio
async def test_unexpected_provider_exception_becomes_failed_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = make_executor(
        monkeypatch,
        AgentFactory(
            FakeAgent(
                error=ConnectionError(
                    "provider connection reset"
                )
            )
        ),
    )

    task = make_task()

    outcome = await executor.execute(
        task,
        make_state(),
    )

    assert outcome.status is TaskStatus.FAILED
    assert "provider connection reset" in (
        outcome.error or ""
    )
    assert task.status is TaskStatus.FAILED


@pytest.mark.asyncio
async def test_failure_preserves_previous_task_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = RecordingTrace()

    executor = make_executor(
        monkeypatch,
        AgentFactory(
            FakeAgent(error=ConnectionError("down"))
        ),
        trace=trace,
    )

    state = make_state(
        attempt=2,
        progress="Mapped the parser.",
        findings=["A"],
        artifacts=["report.md"],
        remaining_work="Run the tests.",
    )

    outcome = await executor.execute(
        make_task(),
        state,
    )

    assert outcome.status is TaskStatus.FAILED
    assert outcome.progress == "Mapped the parser."
    assert outcome.findings == ("A",)
    assert outcome.artifacts == ("report.md",)
    assert outcome.remaining_work == "Run the tests."

    # Prior state is untouched by a failed attempt.
    assert state.progress == "Mapped the parser."
    assert state.findings == ["A"]

    assert [
        event["error"]
        for event in trace.events
        if event["event"] == "task_failed"
    ] == ["ConnectionError"]


@pytest.mark.asyncio
async def test_iteration_exhaustion_becomes_failed_with_partial_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partial = WorkerTaskResult(
        status="completed",
        result={"content": "partial answer"},
        progress="Inspected the layout.",
        findings=["Found X"],
    )

    executor = make_executor(
        monkeypatch,
        AgentFactory(
            FakeAgent(
                error=WorkerIterationExhausted(partial)
            )
        ),
    )

    task = make_task()

    outcome = await executor.execute(
        task,
        make_state(findings=["Earlier finding"]),
    )

    # Runtime fact beats the finalizer's status.
    assert outcome.status is TaskStatus.FAILED
    assert "iteration budget" in (
        outcome.error or ""
    ).lower()
    assert task.status is TaskStatus.FAILED

    # Partial work is preserved, including prior findings.
    assert outcome.result == {"content": "partial answer"}
    assert outcome.progress == "Inspected the layout."
    assert outcome.findings == (
        "Earlier finding",
        "Found X",
    )


@pytest.mark.asyncio
async def test_cancellation_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = make_executor(
        monkeypatch,
        AgentFactory(
            FakeAgent(error=asyncio.CancelledError())
        ),
    )

    with pytest.raises(asyncio.CancelledError):
        await executor.execute(
            make_task(),
            make_state(),
        )
