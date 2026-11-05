"""Supervisor batch isolation.

One Task's failure must never abort its siblings or the Request, and
cancellation must cancel the whole batch rather than orphan Workers.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from supervisor.supervisor import Supervisor
from tasks.models import TaskState
from tasks.outcome import TaskOutcome
from tasks.request import Request
from tasks.task import Task, TaskStatus


class StubExecutor:
    """Deterministic TaskExecutor double keyed by task_id."""

    def __init__(
        self,
        behaviours: dict[str, Any],
    ) -> None:
        self.behaviours = behaviours
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def execute(
        self,
        task: Task,
        state: TaskState,
    ) -> TaskOutcome:
        self.started.set()

        behaviour = self.behaviours[
            task.task_id
        ]

        if behaviour == "hang":
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

        if isinstance(behaviour, BaseException):
            raise behaviour

        return behaviour


def make_task(
    task_id: str,
) -> Task:
    return Task(
        request_id="req_batch",
        task_id=task_id,
        query=f"Work {task_id}.",
        authorized_domains=["coding"],
    )


def make_supervisor(
    tasks: list[Task],
    behaviours: dict[str, Any],
) -> Supervisor:
    supervisor = object.__new__(Supervisor)

    supervisor.request = Request(
        request_id="req_batch",
        query="Do three things.",
    )
    supervisor.trace = None
    supervisor.tasks = {
        task.task_id: task
        for task in tasks
    }
    supervisor.task_states = {
        task.task_id: TaskState(
            task_id=task.task_id,
            instruction=task.query,
            authorized_domains={"coding"},
        )
        for task in tasks
    }
    supervisor.outcomes = {}
    supervisor.task_semaphore = asyncio.Semaphore(
        5
    )
    supervisor.task_executor = StubExecutor(
        behaviours
    )

    return supervisor


def completed(
    task_id: str,
    content: str,
) -> TaskOutcome:
    return TaskOutcome(
        request_id="req_batch",
        task_id=task_id,
        status=TaskStatus.COMPLETED,
        result={"content": content},
    )


def failed(
    task_id: str,
    error: str,
) -> TaskOutcome:
    return TaskOutcome(
        request_id="req_batch",
        task_id=task_id,
        status=TaskStatus.FAILED,
        error=error,
    )


@pytest.mark.asyncio
async def test_one_failed_task_keeps_sibling_outcomes() -> None:
    tasks = [
        make_task("task-a"),
        make_task("task-b"),
        make_task("task-c"),
    ]

    supervisor = make_supervisor(
        tasks,
        {
            "task-a": completed("task-a", "A"),
            "task-b": failed("task-b", "boom"),
            "task-c": completed("task-c", "C"),
        },
    )

    outcomes = await supervisor._execute_batch(
        tasks
    )

    assert len(outcomes) == 3

    statuses = {
        outcome.task_id: outcome.status
        for outcome in outcomes
    }

    assert statuses == {
        "task-a": TaskStatus.COMPLETED,
        "task-b": TaskStatus.FAILED,
        "task-c": TaskStatus.COMPLETED,
    }

    assert outcomes[0].result == {"content": "A"}
    assert outcomes[1].error == "boom"
    assert outcomes[2].result == {"content": "C"}

    assert set(supervisor.outcomes) == {
        "task-a",
        "task-b",
        "task-c",
    }

    assert (
        supervisor.tasks["task-b"].status
        is TaskStatus.FAILED
    )
    assert (
        supervisor.tasks["task-a"].status
        is TaskStatus.COMPLETED
    )


@pytest.mark.asyncio
async def test_unexpected_executor_exception_is_isolated() -> None:
    tasks = [
        make_task("task-a"),
        make_task("task-b"),
        make_task("task-c"),
    ]

    supervisor = make_supervisor(
        tasks,
        {
            "task-a": completed("task-a", "A"),
            "task-b": ConnectionError(
                "MCP unavailable"
            ),
            "task-c": completed("task-c", "C"),
        },
    )

    # The batch completes: no exception escapes.
    outcomes = await supervisor._execute_batch(
        tasks
    )

    assert len(outcomes) == 3

    by_id = {
        outcome.task_id: outcome
        for outcome in outcomes
    }

    assert (
        by_id["task-a"].status
        is TaskStatus.COMPLETED
    )
    assert (
        by_id["task-c"].status
        is TaskStatus.COMPLETED
    )

    crashed = by_id["task-b"]

    assert crashed.status is TaskStatus.FAILED
    assert "Task execution crashed" in (
        crashed.error or ""
    )
    assert "ConnectionError" in (crashed.error or "")

    assert (
        supervisor.tasks["task-b"].status
        is TaskStatus.FAILED
    )


@pytest.mark.asyncio
async def test_request_cancellation_cancels_the_batch() -> None:
    tasks = [
        make_task("task-a"),
        make_task("task-b"),
    ]

    supervisor = make_supervisor(
        tasks,
        {
            "task-a": "hang",
            "task-b": "hang",
        },
    )

    batch = asyncio.create_task(
        supervisor._execute_batch(tasks)
    )

    await asyncio.wait_for(
        supervisor.task_executor.started.wait(),
        timeout=2.0,
    )

    batch.cancel()

    with pytest.raises(asyncio.CancelledError):
        await batch

    await asyncio.sleep(0)

    # Cancellation reached the child Task executions; they do not
    # keep running for a dead Request.
    assert (
        supervisor.task_executor.cancelled.is_set()
    )
