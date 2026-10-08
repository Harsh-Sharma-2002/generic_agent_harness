"""End-to-end concurrency tests for the KernelAI Orchestrator."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.orchestrator import Orchestrator
from tasks.request import Request


RESULTS_PATH = Path(
    "tests/orchestrator_concurrent_results.json"
)


@pytest.mark.asyncio
async def test_orchestrator_ten_concurrent_requests() -> None:
    """
    Submit ten real Requests concurrently through one Orchestrator.

    Results are persisted to disk so the complete execution can be
    inspected after pytest finishes.
    """

    orchestrator = Orchestrator(
        supervisor_max_iterations=10,
        max_concurrent_requests=3,
        max_concurrent_tasks=3,
        verbose=True,
        local=False,
    )

    requests = [
        Request(
            request_id="orchestrator-test-001",
            query=(
                "Determine how many customers are currently stored "
                "in the database."
            ),
        ),
        Request(
            request_id="orchestrator-test-002",
            query=(
                "Determine how many products are currently stored "
                "in the database."
            ),
        ),
        Request(
            request_id="orchestrator-test-003",
            query=(
                "Find the most expensive product in the database "
                "and report its name and price."
            ),
        ),
        Request(
            request_id="orchestrator-test-004",
            query=(
                "Search the web for recent information about "
                "NVIDIA Blackwell GPUs and summarize it."
            ),
        ),
        Request(
            request_id="orchestrator-test-005",
            query=(
                "Search the web for recent information about AMD "
                "AI GPUs and summarize it."
            ),
        ),
        Request(
            request_id="orchestrator-test-006",
            query=(
                "Determine how many orders are currently stored "
                "in the database."
            ),
        ),
        Request(
            request_id="orchestrator-test-007",
            query=(
                "Determine the most expensive product in the "
                "database. Then search the web for recent information "
                "relevant to that exact product."
            ),
        ),
        Request(
            request_id="orchestrator-test-008",
            query=(
                "Independently determine the number of customers "
                "and the number of products in the database, then "
                "give me a combined summary."
            ),
        ),
        Request(
            request_id="orchestrator-test-009",
            query=(
                "Search the web for recent information about NVIDIA "
                "Blackwell GPUs and independently determine how many "
                "customers are in the database. Summarize both."
            ),
        ),
        Request(
            request_id="orchestrator-test-010",
            query=(
                "Find the most expensive product in the database "
                "and the total number of customers. After identifying "
                "the product, search the web for recent information "
                "about that product. Return one combined report."
            ),
        ),
    ]

    async def execute_request(
        request: Request,
    ) -> dict[str, Any]:
        """
        Execute one Request while preserving success or failure
        information for the final results file.
        """

        try:
            result = await orchestrator.submit(
                request
            )

            return {
                "request_id": request.request_id,
                "query": request.query,
                "status": "completed",
                "result": result,
                "error": None,
            }

        except Exception as exc:
            return {
                "request_id": request.request_id,
                "query": request.query,
                "status": "failed",
                "result": None,
                "error": (
                    f"{type(exc).__name__}: {exc}"
                ),
            }

    results = await asyncio.gather(
        *[
            execute_request(request)
            for request in requests
        ]
    )

    completed = [
        result
        for result in results
        if result["status"] == "completed"
    ]

    failed = [
        result
        for result in results
        if result["status"] == "failed"
    ]

    output = {
        "test": "ten_concurrent_requests",
        "configuration": {
            "submitted_requests": len(requests),
            "max_concurrent_requests": (
                orchestrator.max_concurrent_requests
            ),
            "max_concurrent_tasks_per_supervisor": (
                orchestrator.max_concurrent_tasks
            ),
        },
        "summary": {
            "completed": len(completed),
            "failed": len(failed),
        },
        "final_orchestrator_state": {
            "submitted_request_ids": sorted(
                orchestrator.submitted_request_ids
            ),
            "active_request_ids": sorted(
                orchestrator.active_requests.keys()
            ),
        },
        "results": results,
    }

    RESULTS_PATH.write_text(
        json.dumps(
            output,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    print("\n" + "=" * 60)
    print("ORCHESTRATOR CONCURRENCY TEST")
    print("=" * 60)
    print(
        f"Submitted: {len(requests)}"
    )
    print(
        f"Completed: {len(completed)}"
    )
    print(
        f"Failed:    {len(failed)}"
    )
    print(
        f"Results:   {RESULTS_PATH}"
    )

    for result in results:
        print(
            result["request_id"],
            "->",
            result["status"],
        )

    # All Requests must have returned some terminal outcome.
    assert len(results) == 10

    # No Request may remain inside the Orchestrator after the
    # complete gather has returned.
    assert not orchestrator.submitted_request_ids
    assert not orchestrator.active_requests

    # This is an end-to-end healthy-system test, so all ten
    # Requests are expected to complete.
    assert len(completed) == 10
    assert not failed
