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

    assert record["data"] == {
        "system_requests": 5,
        "active_requests": 3,
        "waiting_requests": 2,
    }


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

    assert records[0]["event"] == "request_submitted"
    assert records[1]["event"] == "request_admitted"
    assert records[2]["event"] == "task_delegated"

    assert records[2]["task_id"] == "test-task-001"
    assert records[2]["data"]["skills"] == [
        "text2sql"
    ]


def test_runtime_tracer_sink_failure_isolated(
    tmp_path: Path,
) -> None:
    """
    Failure in one sink must not prevent other sinks from receiving
    the event or propagate into KernelAI execution.
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

    # This must not raise.
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


def test_runtime_tracer_task_event(
    tmp_path: Path,
) -> None:
    """
    Task-scoped events should preserve both Request and Task
    correlation IDs.
    """

    trace_path = tmp_path / "runtime_trace.jsonl"

    tracer = RuntimeTracer(
        sinks=[
            JSONLSink(trace_path),
        ]
    )

    tracer.emit(
        component="task_executor",
        event="task_started",
        request_id="test-request-001",
        task_id="test-task-001",
    )

    record = json.loads(
        trace_path.read_text(
            encoding="utf-8"
        ).strip()
    )

    assert record["component"] == "task_executor"
    assert record["event"] == "task_started"
    assert record["request_id"] == "test-request-001"
    assert record["task_id"] == "test-task-001"

