"""Test Task concurrency limits within one KernelAI Request."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from observability.sinks.jsonl import JSONLSink
from observability.sinks.terminal import TerminalSink
from observability.tracer import RuntimeTracer
from orchestrator.orchestrator import Orchestrator
from tasks.request import Request


TRACE_PATH = Path(
    "tests/single_request_parallel_tasks_trace.jsonl"
)

MAX_CONCURRENT_TASKS = 2


@pytest.mark.asyncio
async def test_single_request_parallel_tasks() -> None:
    """
    Submit one Request containing five independent pieces of work.

    More Tasks may exist than the configured concurrency limit.
    The runtime must ensure that no more than
    MAX_CONCURRENT_TASKS are active at the same time.
    """

    if TRACE_PATH.exists():
        TRACE_PATH.unlink()

    tracer = RuntimeTracer(
        sinks=[
            TerminalSink(),
            JSONLSink(TRACE_PATH),
        ]
    )

    orchestrator = Orchestrator(
        supervisor_max_iterations=10,
        max_concurrent_requests=1,
        max_concurrent_tasks=MAX_CONCURRENT_TASKS,
        verbose=False,
        local=False,
        trace=tracer,
    )

    request = Request(
        request_id="parallel-request-001",
        query=(
            "Complete all five of the following independent tasks and "
            "combine their results into one final response. None of these "
            "tasks depends on the result of another, so delegate all five "
            "independent tasks before waiting for their results.\n\n"

            "1. Determine how many customers are currently stored "
            "in the database.\n"

            "2. Determine how many products are currently stored "
            "in the database.\n"

            "3. Find the most expensive product in the database and "
            "report its name and price.\n"

            "4. Search the web for recent information about NVIDIA "
            "Blackwell GPUs and provide a short summary.\n"

            "5. Search the web for recent information about AMD AI GPUs "
            "and provide a short summary."
        ),
    )

    start_time = time.perf_counter()

    result = await orchestrator.submit(
        request
    )

    end_time = time.perf_counter()

    total_runtime = (
        end_time - start_time
    )

    assert result
    assert result.get("content")

    assert not orchestrator.submitted_request_ids
    assert not orchestrator.active_requests

    assert TRACE_PATH.exists()

    lines = TRACE_PATH.read_text(
        encoding="utf-8"
    ).splitlines()

    records = [
        json.loads(line)
        for line in lines
    ]

    assert records

    supervisor_records = [
        record
        for record in records
        if record["component"] == "supervisor"
    ]

    task_executor_records = [
        record
        for record in records
        if record["component"] == "task_executor"
    ]

    worker_records = [
        record
        for record in records
        if record["component"] == "generic_worker"
    ]

    delegated = [
        record
        for record in supervisor_records
        if record["event"] == "task_delegated"
    ]

    batches_started = [
        record
        for record in supervisor_records
        if record["event"] == "batch_started"
    ]

    task_waiting = [
        record
        for record in supervisor_records
        if record["event"] == "task_waiting"
    ]

    task_started = [
        record
        for record in supervisor_records
        if record["event"] == "task_started"
    ]

    task_completed = [
        record
        for record in task_executor_records
        if record["event"] == "task_completed"
    ]

    task_failed = [
        record
        for record in task_executor_records
        if record["event"] == "task_failed"
    ]

    task_worker_records = [
        record
        for record in worker_records
        if record.get("worker_role") == "task"
    ]

    # The original Request requires at least five independent Tasks.
    #
    # The Supervisor may legitimately create additional Tasks after
    # receiving outcomes from an earlier batch.

    assert len(delegated) >= 5

    delegated_ids = {
        record["task_id"]
        for record in delegated
    }

    assert len(delegated_ids) >= 5

    # The initial planning step should create the five requested Tasks
    # together in one batch.

    assert batches_started
    assert batches_started[0]["task_count"] == 5

    # Every delegated Task should enter the execution path.

    waiting_ids = {
        record["task_id"]
        for record in task_waiting
    }

    started_ids = {
        record["task_id"]
        for record in task_started
    }

    completed_ids = {
        record["task_id"]
        for record in task_completed
    }

    failed_ids = {
        record["task_id"]
        for record in task_failed
    }

    terminal_ids = (
        completed_ids
        | failed_ids
    )

    assert delegated_ids == waiting_ids
    assert delegated_ids == started_ids
    assert delegated_ids == terminal_ids

    # Reconstruct active Task count from the global event stream.
    #
    # TASK_STARTED means the Task acquired a Supervisor execution
    # slot.
    #
    # TASK_COMPLETED / TASK_FAILED means that slot was released.

    active_task_ids: set[str] = set()
    max_active_tasks = 0

    for record in records:
        component = record["component"]
        event = record["event"]
        task_id = record["task_id"]

        if (
            component == "supervisor"
            and event == "task_started"
        ):
            assert task_id is not None

            # A Task cannot acquire an execution slot twice without
            # first leaving the active set.
            assert task_id not in active_task_ids

            active_task_ids.add(
                task_id
            )

            max_active_tasks = max(
                max_active_tasks,
                len(active_task_ids),
            )

            # Core concurrency invariant.
            assert (
                len(active_task_ids)
                <= MAX_CONCURRENT_TASKS
            )

        elif (
            component == "task_executor"
            and event in {
                "task_completed",
                "task_failed",
            }
        ):
            assert task_id is not None

            # A terminal Task must previously have been active.
            assert task_id in active_task_ids

            active_task_ids.remove(
                task_id
            )

    # No Task may remain active after the Request completes.

    assert not active_task_ids

    # This workload contains enough independent work to actually
    # saturate the configured Task concurrency limit.

    assert (
        max_active_tasks
        == MAX_CONCURRENT_TASKS
    )

    # Prove that the semaphore actually blocked work.
    #
    # Before the first active Task completed, exactly
    # MAX_CONCURRENT_TASKS Tasks should have crossed TASK_STARTED,
    # while all five initial Tasks had already entered TASK_WAITING.

    first_completion_index = next(
        index
        for index, record in enumerate(records)
        if (
            record["component"] == "task_executor"
            and record["event"] in {
                "task_completed",
                "task_failed",
            }
        )
    )

    records_before_first_completion = (
        records[:first_completion_index]
    )

    waiting_before_first_completion = [
        record
        for record in records_before_first_completion
        if (
            record["component"] == "supervisor"
            and record["event"] == "task_waiting"
        )
    ]

    started_before_first_completion = [
        record
        for record in records_before_first_completion
        if (
            record["component"] == "supervisor"
            and record["event"] == "task_started"
        )
    ]

    assert len(waiting_before_first_completion) == 5

    assert (
        len(started_before_first_completion)
        == MAX_CONCURRENT_TASKS
    )

    # Therefore some Tasks were waiting behind the execution limit.

    blocked_before_first_completion = (
        len(waiting_before_first_completion)
        - len(started_before_first_completion)
    )

    assert blocked_before_first_completion > 0

    # Every Task Worker event must remain correlated with this Request
    # and with a Task created by the Supervisor.

    assert task_worker_records

    assert all(
        record["request_id"] == request.request_id
        for record in task_worker_records
    )

    assert all(
        record["task_id"] in delegated_ids
        for record in task_worker_records
    )

    print("\n" + "=" * 70)
    print("SINGLE REQUEST / TASK CONCURRENCY TEST")
    print("=" * 70)
    print(f"Request ID:              {request.request_id}")
    print(f"Total Tasks delegated:   {len(delegated_ids)}")
    print(f"Execution batches:       {len(batches_started)}")
    print(
        f"Configured max active:   "
        f"{MAX_CONCURRENT_TASKS}"
    )
    print(
        f"Observed max active:     "
        f"{max_active_tasks}"
    )
    print(
        f"Initially waiting:       "
        f"{len(waiting_before_first_completion)}"
    )
    print(
        f"Initially started:       "
        f"{len(started_before_first_completion)}"
    )
    print(
        f"Initially blocked:       "
        f"{blocked_before_first_completion}"
    )
    print(f"Task completions:        {len(task_completed)}")
    print(f"Task failures:           {len(task_failed)}")
    print(f"Total events:            {len(records)}")
    print(f"Total runtime:           {total_runtime:.2f} seconds")
    print(f"Trace file:              {TRACE_PATH}")
    print("=" * 70)