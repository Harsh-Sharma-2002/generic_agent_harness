"""Tests for KernelAI observability infrastructure."""

from __future__ import annotations

import json
from pathlib import Path

from observability.sinks.jsonl import JSONLSink
from observability.sinks.terminal import TerminalSink
from observability.tracer import RuntimeTracer


def test_runtime_tracer_terminal_and_jsonl(
    tmp_path: Path,
    capsys,
) -> None:
    """
    One emitted event should reach both the terminal and JSONL
    sinks with the same event data.
    """

    trace_path = tmp_path / "runtime_trace.jsonl"

    tracer = RuntimeTracer(
        sinks=[
            TerminalSink(),
            JSONLSink(trace_path),
        ]
    )

    tracer.emit(
        component="orchestrator",
        event="request_admitted",
        request_id="test-request-001",
        system_requests=5,
        active_requests=3,
        waiting_requests=2,
    )

    # Check terminal output.
    captured = capsys.readouterr()

    assert "ORCHESTRATOR" in captured.out
    assert "test-request-001" in captured.out
    assert "REQUEST_ADMITTED" in captured.out
    assert "system_requests=5" in captured.out
    assert "active_requests=3" in captured.out
    assert "waiting_requests=2" in captured.out

    # Check persistent JSONL output.
    assert trace_path.exists()

    lines = trace_path.read_text(
        encoding="utf-8"
    ).splitlines()

    assert len(lines) == 1

    record = json.loads(
        lines[0]
    )

    assert record["timestamp"]
    assert record["component"] == "orchestrator"
    assert record["event"] == "request_admitted"
    assert record["request_id"] == "test-request-001"
    assert record["task_id"] is None

    # Event metadata is flattened into the JSONL record.
    assert record["system_requests"] == 5
    assert record["active_requests"] == 3
    assert record["waiting_requests"] == 2

    assert "data" not in record


def test_runtime_tracer_multiple_events(
    tmp_path: Path,
) -> None:
    """
    Multiple emitted events should be appended to the JSONL trace
    in emission order.
    """

    trace_path = tmp_path / "runtime_trace.jsonl"

    tracer = RuntimeTracer(
        sinks=[
            JSONLSink(trace_path),
        ]
    )

    tracer.emit(
        component="orchestrator",
        event="request_submitted",
        request_id="test-request-001",
    )

    tracer.emit(
        component="orchestrator",
        event="request_admitted",
        request_id="test-request-001",
    )

    tracer.emit(
        component="supervisor",
        event="task_delegated",
        request_id="test-request-001",
        task_id="test-task-001",
        skills=["text2sql"],
    )

    lines = trace_path.read_text(
        encoding="utf-8"
    ).splitlines()

    assert len(lines) == 3

    records = [
        json.loads(line)
        for line in lines
    ]

    assert [
        record["event"]
        for record in records
    ] == [
        "request_submitted",
        "request_admitted",
        "task_delegated",
    ]

    assert records[0]["request_id"] == "test-request-001"
    assert records[0]["task_id"] is None

    assert records[1]["request_id"] == "test-request-001"
    assert records[1]["task_id"] is None

    assert records[2]["request_id"] == "test-request-001"
    assert records[2]["task_id"] == "test-task-001"

    assert records[2]["skills"] == [
        "text2sql"
    ]

    assert all(
        "data" not in record
        for record in records
    )


def test_runtime_tracer_worker_metadata(
    tmp_path: Path,
) -> None:
    """
    GenericWorker metadata should be persisted as flattened
    JSONL fields.
    """

    trace_path = tmp_path / "runtime_trace.jsonl"

    tracer = RuntimeTracer(
        sinks=[
            JSONLSink(trace_path),
        ]
    )

    tracer.emit(
        component="generic_worker",
        event="llm_call_started",
        request_id="test-request-001",
        task_id="test-task-001",
        worker_role="task",
        iteration=2,
    )

    lines = trace_path.read_text(
        encoding="utf-8"
    ).splitlines()

    assert len(lines) == 1

    record = json.loads(
        lines[0]
    )

    assert record["component"] == "generic_worker"
    assert record["event"] == "llm_call_started"
    assert record["request_id"] == "test-request-001"
    assert record["task_id"] == "test-task-001"
    assert record["worker_role"] == "task"
    assert record["iteration"] == 2

    assert "data" not in record


def test_runtime_tracer_tool_failure_metadata(
    tmp_path: Path,
    capsys,
) -> None:
    """
    Tool failure diagnostics should appear in both terminal and
    persistent JSONL output.
    """

    trace_path = tmp_path / "runtime_trace.jsonl"

    tracer = RuntimeTracer(
        sinks=[
            TerminalSink(),
            JSONLSink(trace_path),
        ]
    )

    tracer.emit(
        component="generic_worker",
        event="tool_call_failed",
        request_id="test-request-005",
        task_id="test-task-005",
        worker_role="task",
        iteration=3,
        tool_name="web_search",
        error="RuntimeError",
        error_message="Web search backend failed.",
    )

    # Check terminal output.
    captured = capsys.readouterr()

    assert "GENERIC_WORKER" in captured.out
    assert "test-request-005" in captured.out
    assert "test-task-005" in captured.out
    assert "TOOL_CALL_FAILED" in captured.out
    assert "worker_role=task" in captured.out
    assert "iteration=3" in captured.out
    assert "tool_name=web_search" in captured.out
    assert "error=RuntimeError" in captured.out
    assert (
        'error_message="Web search backend failed."'
        in captured.out
    )

    # Check persistent JSONL output.
    lines = trace_path.read_text(
        encoding="utf-8"
    ).splitlines()

    assert len(lines) == 1

    record = json.loads(
        lines[0]
    )

    assert record["component"] == "generic_worker"
    assert record["event"] == "tool_call_failed"
    assert record["request_id"] == "test-request-005"
    assert record["task_id"] == "test-task-005"
    assert record["worker_role"] == "task"
    assert record["iteration"] == 3
    assert record["tool_name"] == "web_search"
    assert record["error"] == "RuntimeError"
    assert (
        record["error_message"]
        == "Web search backend failed."
    )

    assert "data" not in record


def test_runtime_tracer_sink_failure_isolated(
    tmp_path: Path,
) -> None:
    """
    A failing sink must not prevent other sinks from receiving
    the same runtime event.
    """

    class FailingSink:
        def emit(
            self,
            event,
        ) -> None:
            raise RuntimeError(
                "Intentional sink failure."
            )

    trace_path = tmp_path / "runtime_trace.jsonl"

    tracer = RuntimeTracer(
        sinks=[
            FailingSink(),
            JSONLSink(trace_path),
        ]
    )

    tracer.emit(
        component="orchestrator",
        event="request_submitted",
        request_id="test-request-001",
    )

    assert trace_path.exists()

    lines = trace_path.read_text(
        encoding="utf-8"
    ).splitlines()

    assert len(lines) == 1

    record = json.loads(
        lines[0]
    )

    assert record["event"] == "request_submitted"
    assert record["request_id"] == "test-request-001"