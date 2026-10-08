"""End-to-end tests for the KernelAI Supervisor runtime."""

from __future__ import annotations

import pytest

from supervisor.supervisor import Supervisor
from tasks.request import Request
from tasks.task import TaskStatus


# ============================================================
# 1. Single-task delegation
# ============================================================


@pytest.mark.asyncio
async def test_supervisor_single_database_task() -> None:
    """
    Supervisor should discover the appropriate skill,
    delegate one database Task, consume its outcome,
    and answer the original Request.
    """

    request = Request(
        request_id="supervisor-test-001",
        query=(
            "Find out how many customers are currently stored "
            "in the database and give me the answer."
        ),
    )

    supervisor = Supervisor(
        request=request,
        max_iterations=10,
        verbose=True,
        local=False,
    )

    result = await supervisor.run()

    assert result
    assert result["content"]
    assert "100" in result["content"]

    assert len(supervisor.tasks) >= 1
    assert len(supervisor.outcomes) >= 1

    assert all(
        outcome.status == TaskStatus.COMPLETED
        for outcome in supervisor.outcomes.values()
    )

    print("\n" + "=" * 60)
    print("SUPERVISOR SINGLE TASK RESULT")
    print("=" * 60)
    print(result["content"])


# ============================================================
# 2. Two independent Tasks
# ============================================================


@pytest.mark.asyncio
async def test_supervisor_two_independent_tasks() -> None:
    """
    Supervisor should recognize two independent pieces of work,
    delegate them separately, and synthesize both outcomes.
    """

    request = Request(
        request_id="supervisor-test-002",
        query=(
            "Do two independent things: "
            "find recent information about NVIDIA Blackwell GPUs "
            "from the web, and separately determine how many "
            "customers are currently stored in the database. "
            "Then summarize both findings."
        ),
    )

    supervisor = Supervisor(
        request=request,
        max_iterations=10,
        verbose=True,
        local=False,
    )

    result = await supervisor.run()

    assert result
    assert result["content"]

    assert "100" in result["content"]

    assert len(supervisor.tasks) >= 2
    assert len(supervisor.outcomes) >= 2

    assert all(
        outcome.status == TaskStatus.COMPLETED
        for outcome in supervisor.outcomes.values()
    )

    print("\n" + "=" * 60)
    print("SUPERVISOR TWO-TASK RESULT")
    print("=" * 60)
    print(result["content"])


# ============================================================
# 3. Larger independent batch
# ============================================================


@pytest.mark.asyncio
async def test_supervisor_large_independent_batch() -> None:
    """
    Stress the Supervisor's decomposition and batching behavior
    with several mutually independent jobs.
    """

    request = Request(
        request_id="supervisor-test-003",
        query=(
            "Complete these four independent investigations and "
            "return one combined report:\n"
            "1. Find recent web information about NVIDIA Blackwell GPUs.\n"
            "2. Find recent web information about AMD's current AI GPUs.\n"
            "3. Determine how many customers are in the database.\n"
            "4. Determine how many products are in the database.\n"
            "\n"
            "These investigations do not depend on one another."
        ),
    )

    supervisor = Supervisor(
        request=request,
        max_iterations=10,
        verbose=True,
        local=False,
    )

    result = await supervisor.run()

    assert result
    assert result["content"]

    assert len(supervisor.tasks) >= 4
    assert len(supervisor.outcomes) >= 4

    assert all(
        outcome.status == TaskStatus.COMPLETED
        for outcome in supervisor.outcomes.values()
    )

    assert "100" in result["content"]

    print("\n" + "=" * 60)
    print("SUPERVISOR LARGE BATCH RESULT")
    print("=" * 60)
    print(result["content"])


# ============================================================
# 4. Sequential dependency / replanning
# ============================================================


@pytest.mark.asyncio
async def test_supervisor_replans_after_task_outcome() -> None:
    """
    Test a request where the second piece of work cannot be known
    until the first Task has returned.

    This should force at least two planning points rather than one
    independent batch.
    """

    request = Request(
        request_id="supervisor-test-004",
        query=(
            "First query the database and determine the name of the "
            "most expensive product currently stored there. "
            "Only after you know that product name, search the web "
            "for recent information about that exact product. "
            "Then summarize the database finding and web finding."
        ),
    )

    supervisor = Supervisor(
        request=request,
        max_iterations=10,
        verbose=True,
        local=False,
    )

    result = await supervisor.run()

    assert result
    assert result["content"]

    # We expect at least:
    #
    # Task 1 -> database lookup
    # outcome returned to Supervisor
    # Task 2 -> web search based on Task 1 result
    #
    assert len(supervisor.tasks) >= 2
    assert len(supervisor.outcomes) >= 2

    assert all(
        outcome.status == TaskStatus.COMPLETED
        for outcome in supervisor.outcomes.values()
    )

    print("\n" + "=" * 60)
    print("SUPERVISOR REPLANNING RESULT")
    print("=" * 60)
    print(result["content"])


# ============================================================
# 5. Complex decomposition:
#    parallel first batch + dependent second planning point
# ============================================================


@pytest.mark.asyncio
async def test_supervisor_parallel_then_dependent_replanning() -> None:
    """
    Exercise both architectural behaviors in one Request:

    - multiple independent Tasks in the first planning batch,
    - followed by new work whose definition depends on those outcomes.
    """

    request = Request(
        request_id="supervisor-test-005",
        query=(
            "Build a short comparison report using this process:\n"
            "\n"
            "First, independently determine:\n"
            "1. the most expensive product in the database, and\n"
            "2. the number of customers in the database.\n"
            "\n"
            "After the most expensive product has been identified, "
            "search the web for recent information about that exact "
            "product. Do not guess the product before reading the "
            "database result.\n"
            "\n"
            "Finally combine the product information, web research, "
            "and customer count into one concise report."
        ),
    )

    supervisor = Supervisor(
        request=request,
        max_iterations=10,
        verbose=True,
        local=False,
    )

    result = await supervisor.run()

    assert result
    assert result["content"]

    # First planning point should need two independent DB Tasks.
    # A later planning point should create the dependent web Task.
    assert len(supervisor.tasks) >= 3
    assert len(supervisor.outcomes) >= 3

    assert all(
        outcome.status == TaskStatus.COMPLETED
        for outcome in supervisor.outcomes.values()
    )

    assert "100" in result["content"]

    print("\n" + "=" * 60)
    print("SUPERVISOR COMPLEX REPLANNING RESULT")
    print("=" * 60)
    print(result["content"])
