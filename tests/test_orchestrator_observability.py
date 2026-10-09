"""End-to-end KernelAI observability test."""

from __future__ import annotations

import asyncio
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
    "tests/orchestrator_observability_trace.jsonl"
)


@pytest.mark.asyncio
async def test_five_concurrent_requests_with_observability() -> None:
    """
    Submit five real Requests concurrently while allowing only two
    Requests to execute at once.

    Verify Request admission, Supervisor execution, Task execution,
    GenericWorker execution, LLM calls, tool calls, and the final
    runtime state through the observability trace.
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
        max_concurrent_requests=2,
        max_concurrent_tasks=3,
        verbose=False,
        local=False,
        trace=tracer,
    )

    requests = [
        Request(
            request_id="obs-request-001",
            query=(
                "Determine how many customers are currently "
                "stored in the database."
            ),
        ),
        Request(
            request_id="obs-request-002",
            query=(
                "Determine how many products are currently "
                "stored in the database."
            ),
        ),
        Request(
            request_id="obs-request-003",
            query=(
                "Find the most expensive product in the database "
                "and report its name and price."
            ),
        ),
        Request(
            request_id="obs-request-004",
            query=(
                "Search the web for recent information about "
                "NVIDIA Blackwell GPUs and give me a short summary."
            ),
        ),
        Request(
            request_id="obs-request-005",
            query=(
                "Search the web for recent information about AMD "
                "AI GPUs and give me a short summary."
            ),
        ),
    ]

    start_time = time.perf_counter()

    results = await asyncio.gather(
        *[
            orchestrator.submit(request)
            for request in requests
        ]
    )

    end_time = time.perf_counter()

    total_runtime = (
        end_time - start_time
    )

    assert len(results) == 5

    assert all(
        result
        and result.get("content")
        for result in results
    )

    # All Requests must have completely left KernelAI.

    assert not orchestrator.submitted_request_ids
    assert not orchestrator.active_requests

    # Persistent trace must exist.

    assert TRACE_PATH.exists()

    lines = TRACE_PATH.read_text(
        encoding="utf-8"
    ).splitlines()

    records = [
        json.loads(line)
        for line in lines
    ]

    assert records

    # Separate records by runtime component.

    orchestrator_records = [
        record
        for record in records
        if record["component"] == "orchestrator"
    ]

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

    # Request lifecycle.

    submitted = [
        record
        for record in orchestrator_records
        if record["event"] == "request_submitted"
    ]

    admitted = [
        record
        for record in orchestrator_records
        if record["event"] == "request_admitted"
    ]

    completed = [
        record
        for record in orchestrator_records
        if record["event"] == "request_completed"
    ]

    failed = [
        record
        for record in orchestrator_records
        if record["event"] == "request_failed"
    ]

    cancelled = [
        record
        for record in orchestrator_records
        if record["event"] == "request_cancelled"
    ]

    assert len(submitted) == 5
    assert len(admitted) == 5
    assert len(completed) == 5

    assert not failed
    assert not cancelled

    # Verify every Request appears throughout its lifecycle.

    expected_ids = {
        request.request_id
        for request in requests
    }

    submitted_ids = {
        record["request_id"]
        for record in submitted
    }

    admitted_ids = {
        record["request_id"]
        for record in admitted
    }

    completed_ids = {
        record["request_id"]
        for record in completed
    }

    assert submitted_ids == expected_ids
    assert admitted_ids == expected_ids
    assert completed_ids == expected_ids

    # Admission control.
    #
    # Only two Supervisors may be active at once.

    assert all(
        record["active_requests"] <= 2
        for record in orchestrator_records
    )

    # Because five Requests are submitted with capacity for only two,
    # the trace must show Requests waiting for admission.

    assert any(
        record["waiting_requests"] > 0
        for record in orchestrator_records
    )

    # At some point both available Request slots should be occupied.

    assert any(
        record["active_requests"] == 2
        for record in orchestrator_records
    )

    # Supervisor lifecycle.

    supervisor_started = [
        record
        for record in supervisor_records
        if record["event"] == "supervisor_started"
    ]

    supervisor_completed = [
        record
        for record in supervisor_records
        if record["event"] == "supervisor_completed"
    ]

    supervisor_failed = [
        record
        for record in supervisor_records
        if record["event"] == "supervisor_failed"
    ]

    assert len(supervisor_started) == 5
    assert len(supervisor_completed) == 5
    assert not supervisor_failed

    assert {
        record["request_id"]
        for record in supervisor_started
    } == expected_ids

    assert {
        record["request_id"]
        for record in supervisor_completed
    } == expected_ids

    # Task delegation and execution.

    delegated_tasks = [
        record
        for record in supervisor_records
        if record["event"] == "task_delegated"
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

    assert delegated_tasks
    assert task_waiting
    assert task_started
    assert task_completed
    assert not task_failed

    delegated_task_ids = {
        record["task_id"]
        for record in delegated_tasks
    }

    waiting_task_ids = {
        record["task_id"]
        for record in task_waiting
    }

    started_task_ids = {
        record["task_id"]
        for record in task_started
    }

    completed_task_ids = {
        record["task_id"]
        for record in task_completed
    }

    # Every delegated Task must pass through the complete Task
    # execution lifecycle.

    assert delegated_task_ids == waiting_task_ids
    assert delegated_task_ids == started_task_ids
    assert delegated_task_ids == completed_task_ids

    # GenericWorker roles.

    supervisor_worker_records = [
        record
        for record in worker_records
        if record.get("worker_role") == "supervisor"
    ]

    task_worker_records = [
        record
        for record in worker_records
        if record.get("worker_role") == "task"
    ]

    assert supervisor_worker_records
    assert task_worker_records

    # Supervisor GenericWorkers belong to Requests, not Tasks.

    assert all(
        record["task_id"] is None
        for record in supervisor_worker_records
    )

    # Task GenericWorkers must always be correlated with a Task.

    assert all(
        record["task_id"] is not None
        for record in task_worker_records
    )

    # Every Task Worker must belong to a delegated Task.

    assert {
        record["task_id"]
        for record in task_worker_records
    }.issubset(
        delegated_task_ids
    )

    # Worker lifecycle.

    supervisor_worker_started = [
        record
        for record in supervisor_worker_records
        if record["event"] == "worker_started"
    ]

    task_worker_started = [
        record
        for record in task_worker_records
        if record["event"] == "worker_started"
    ]

    task_worker_completed = [
        record
        for record in task_worker_records
        if record["event"] == "worker_completed"
    ]

    assert supervisor_worker_started
    assert task_worker_started
    assert task_worker_completed

    # LLM observability.

    supervisor_llm_started = [
        record
        for record in supervisor_worker_records
        if record["event"] == "llm_call_started"
    ]

    supervisor_llm_completed = [
        record
        for record in supervisor_worker_records
        if record["event"] == "llm_call_completed"
    ]

    task_llm_started = [
        record
        for record in task_worker_records
        if record["event"] == "llm_call_started"
    ]

    task_llm_completed = [
        record
        for record in task_worker_records
        if record["event"] == "llm_call_completed"
    ]

    assert supervisor_llm_started
    assert supervisor_llm_completed
    assert task_llm_started
    assert task_llm_completed

    # Every successful LLM call in this healthy-system test should
    # have a corresponding completion event.

    assert (
        len(supervisor_llm_started)
        == len(supervisor_llm_completed)
    )

    assert (
        len(task_llm_started)
        == len(task_llm_completed)
    )

    # Tool observability.

    supervisor_tool_started = [
        record
        for record in supervisor_worker_records
        if record["event"] == "tool_call_started"
    ]

    supervisor_tool_completed = [
        record
        for record in supervisor_worker_records
        if record["event"] == "tool_call_completed"
    ]

    task_tool_started = [
        record
        for record in task_worker_records
        if record["event"] == "tool_call_started"
    ]

    task_tool_completed = [
        record
        for record in task_worker_records
        if record["event"] == "tool_call_completed"
    ]

    assert supervisor_tool_started
    assert supervisor_tool_completed

    assert task_tool_started
    assert task_tool_completed

    # The Supervisor should only use its control capabilities.

    assert all(
        record["tool_name"] in {
            "list_skills",
            "delegate_task",
        }
        for record in supervisor_tool_started
    )

    # Task Workers should never use Supervisor control tools.

    assert all(
        record["tool_name"] not in {
            "list_skills",
            "delegate_task",
        }
        for record in task_tool_started
    )

    # JSONL metadata must use the new flattened structure.

    assert all(
        "data" not in record
        for record in records
    )

    print("\n" + "=" * 60)
    print("KERNELAI OBSERVABILITY TEST")
    print("=" * 60)
    print(f"Requests:              {len(requests)}")
    print("Max concurrent:        2")
    print(f"Trace file:            {TRACE_PATH}")
    print(f"Total events:          {len(records)}")
    print(f"Orchestrator events:   {len(orchestrator_records)}")
    print(f"Supervisor events:     {len(supervisor_records)}")
    print(f"TaskExecutor events:   {len(task_executor_records)}")
    print(f"GenericWorker events:  {len(worker_records)}")
    print(f"Delegated Tasks:       {len(delegated_task_ids)}")
    print(f"Supervisor LLM calls:  {len(supervisor_llm_started)}")
    print(f"Task LLM calls:        {len(task_llm_started)}")
    print(f"Supervisor tool calls: {len(supervisor_tool_started)}")
    print(f"Task tool calls:       {len(task_tool_started)}")
    print(f"Start counter:         {start_time:.6f}")
    print(f"End counter:           {end_time:.6f}")
    print(f"Total runtime:         {total_runtime:.2f} seconds")
    print("=" * 60)