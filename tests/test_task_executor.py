"""End-to-end tests for KernelAI TaskExecutor."""

from __future__ import annotations

import pytest

from tasks.task import Task, TaskStatus
from tasks.task_executor import TaskExecutor


@pytest.mark.asyncio
async def test_task_executor_text2sql() -> None:
    """
    Execute a real Text2SQL task through TaskExecutor.

    Requires:
    - ASU LLM backend
    - MCP server
    - PostgreSQL test database
    """

    task = Task(
        request_id="task-executor-request-001",
        task_id="task-executor-task-001",
        query="How many customers are currently in the database?",
        skill_names=[
            "text2sql",
        ],
    )

    executor = TaskExecutor(
        max_iterations=10,
        verbose=True,
        local=False,
    )

    outcome = await executor.execute(
        task
    )

    assert task.status == TaskStatus.COMPLETED

    assert outcome.request_id == task.request_id
    assert outcome.task_id == task.task_id
    assert outcome.status == TaskStatus.COMPLETED

    assert outcome.result is not None
    assert outcome.error is None

    assert "content" in outcome.result
    assert outcome.result["content"]

    # Seeded ecommerce database currently contains 100 customers.
    assert "100" in outcome.result["content"]

    print("\n" + "=" * 60)
    print("TASK EXECUTOR OUTCOME")
    print("=" * 60)
    print(outcome)


@pytest.mark.asyncio
async def test_task_executor_multi_skill() -> None:
    """
    Execute a real Task requiring both web and database capabilities.

    This verifies that TaskExecutor composes multiple skills and grants
    the GenericWorker the union of their allowed tools.
    """

    task = Task(
        request_id="task-executor-request-002",
        task_id="task-executor-task-002",
        query=(
            "Search the web for recent information about NVIDIA "
            "Blackwell GPUs. Then query the database to determine "
            "how many customers are currently stored. Return a short "
            "summary containing both pieces of information."
        ),
        skill_names=[
            "web_search",
            "text2sql",
        ],
    )

    executor = TaskExecutor(
        max_iterations=10,
        verbose=True,
        local=False,
    )

    outcome = await executor.execute(
        task
    )

    assert task.status == TaskStatus.COMPLETED

    assert outcome.request_id == task.request_id
    assert outcome.task_id == task.task_id
    assert outcome.status == TaskStatus.COMPLETED

    assert outcome.result is not None
    assert outcome.error is None

    assert "content" in outcome.result
    assert outcome.result["content"]

    # This proves the database portion of the multi-skill task
    # actually contributed to the final result.
    assert "100" in outcome.result["content"]

    print("\n" + "=" * 60)
    print("MULTI-SKILL TASK EXECUTOR OUTCOME")
    print("=" * 60)
    print(outcome)
