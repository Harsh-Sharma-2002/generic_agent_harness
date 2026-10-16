"""Integration test for a full KernelAI coding request."""

from __future__ import annotations

from pathlib import Path

import pytest

from observability.sinks.jsonl import JSONLSink
from observability.sinks.terminal import TerminalSink
from observability.tracer import RuntimeTracer
from orchestrator.orchestrator import Orchestrator
from tasks.request import Request


TRACE_PATH = Path(
    "tests/coding_skill_trace.jsonl"
)

WORKSPACE = Path(
    "/Users/harshsharma/Desktop/Work_Dir/workspace"
)


@pytest.mark.asyncio
async def test_coding_skill_lru_project() -> None:
    """
    Exercise the complete coding workflow with implementation,
    independent test authoring, validation, and possible repair.
    """

    # --------------------------------------------------------
    # Clean previous trace
    # --------------------------------------------------------

    if TRACE_PATH.exists():
        TRACE_PATH.unlink()

    # --------------------------------------------------------
    # Observability
    # --------------------------------------------------------

    tracer = RuntimeTracer(
        sinks=[
            TerminalSink(),
            JSONLSink(
                TRACE_PATH
            ),
        ]
    )

    # --------------------------------------------------------
    # Runtime
    # --------------------------------------------------------

    orchestrator = Orchestrator(
        supervisor_max_iterations=10,
        max_concurrent_requests=1,
        max_concurrent_tasks=5,
        verbose=False,
        local=False,
        trace=tracer,
    )

    # --------------------------------------------------------
    # Request
    # --------------------------------------------------------

    request = Request(
        request_id="coding-lru-test-001",
        query="""
Create a small Python project in the workspace that implements an
LRUCache class with get(key) and put(key, value) operations.

The cache must have a configurable positive capacity and both operations
should have O(1) average-time complexity.

Write comprehensive pytest tests covering:

- normal operation,
- eviction order,
- updating an existing key,
- capacity of 1,
- missing keys,
- invalid capacity.

Implementation and test authoring must be performed as independent Tasks
so that the implementation worker does not receive the test-authoring
worker's private context and the test-authoring worker independently
derives its tests from the specification.

The implementation Task should create the implementation but must not
write the test suite.

The test-authoring Task should independently create the pytest test suite
from the requirements and public behavior. It must not modify the
implementation.

Once implementation and test authoring are complete, validate the combined
project by running the full pytest suite.

If validation fails because of an implementation defect, perform the
necessary debugging and repair, then rerun the tests.

Only report successful completion after the full test suite passes.
""".strip(),
    )

    # --------------------------------------------------------
    # Execute
    # --------------------------------------------------------

    result = await orchestrator.submit(
        request
    )

    # --------------------------------------------------------
    # Basic runtime validation
    # --------------------------------------------------------

    assert result is not None

    assert TRACE_PATH.exists()

    assert TRACE_PATH.stat().st_size > 0

    # --------------------------------------------------------
    # Workspace validation
    # --------------------------------------------------------

    assert WORKSPACE.exists()

    # At least one Python implementation should have been created.
    implementation_files = [
        path
        for path in WORKSPACE.rglob("*.py")
        if "test" not in path.name.lower()
        and "tests" not in path.parts
        and ".venv" not in path.parts
    ]

    assert implementation_files, (
        "No Python implementation files were created."
    )

    # At least one independently authored pytest file should exist.
    test_files = [
        path
        for path in WORKSPACE.rglob("test_*.py")
        if ".venv" not in path.parts
    ]

    assert test_files, (
        "No pytest test files were created."
    )