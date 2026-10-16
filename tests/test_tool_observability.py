"""Tool observability upgrade tests.

Covers sanitized/bounded tool-call events, tool_call_id correlation,
malformed-argument event paths, terminal capping, and error scrubbing.

These tests are observability-only: they must not change worker
execution behavior.
"""

import json
from datetime import datetime, timezone

import pytest
from openai.types.chat import (
    ChatCompletionMessage,
    ChatCompletionMessageToolCall,
)

from observability.event import RuntimeEvent
from observability.projection import _current_tool
from observability.sanitize import (
    CONSOLE_VALUE_CHAR_LIMIT,
    MAX_ARGUMENTS_BYTES,
    MAX_RESULT_PREVIEW_BYTES,
    build_arguments_trace,
    build_result_trace,
    cap_value,
    redact_secrets,
    scrub_error_message,
)
from observability.sinks.terminal import TerminalSink
from observability.tracer import RuntimeTracer
from worker.generic_worker import GenericWorker


# ============================================================
# Fakes
# ============================================================


class ListSink:
    """Collect RuntimeEvents in memory for assertions."""

    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []

    def emit(self, event: RuntimeEvent) -> None:
        self.events.append(event)


class FakeLLMCaller:
    """Return a scripted sequence of assistant messages."""

    def __init__(self, responses: list[ChatCompletionMessage]) -> None:
        self.responses = list(responses)
        self.calls: list[list] = []

    async def call(self, messages, tools=None, max_tokens=2048):
        self.calls.append(messages)
        return self.responses.pop(0)


class FakeToolClient:
    """Scripted tool client for worker tests."""

    def __init__(self, behaviors: dict[str, object]) -> None:
        self.behaviors = behaviors

    async def get_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"Tool {name}",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            }
            for name in self.behaviors
        ]

    async def call_tool(self, name: str, arguments: dict) -> str:
        behavior = self.behaviors[name]

        if isinstance(behavior, Exception):
            raise behavior

        if callable(behavior):
            return behavior(arguments)

        return behavior


def make_tool_call(
    call_id: str,
    name: str,
    arguments: str,
) -> ChatCompletionMessageToolCall:
    return ChatCompletionMessageToolCall(
        id=call_id,
        type="function",
        function={
            "name": name,
            "arguments": arguments,
        },
    )


def make_worker(
    responses: list[ChatCompletionMessage],
    behaviors: dict[str, object],
) -> tuple[GenericWorker, ListSink]:
    sink = ListSink()

    worker = GenericWorker(
        tools=FakeToolClient(behaviors),
        system_prompt="test system prompt",
        allowed_tools=set(behaviors),
        max_iterations=10,
        verbose=False,
        local=True,
        trace=RuntimeTracer(sinks=[sink]),
    )

    worker.llm = FakeLLMCaller(responses)

    return worker, sink


def events_named(
    sink: ListSink,
    event_name: str,
) -> list[RuntimeEvent]:
    return [
        event
        for event in sink.events
        if event.event == event_name
    ]


# ============================================================
# 9. Sanitization helpers
# ============================================================


def test_sanitize_arguments_non_mutation_and_redaction() -> None:
    """
    Argument sanitization must never mutate the original arguments,
    must redact secret keys recursively, and must produce the
    canonical arguments trace schema.
    """

    original = {
        "query": "SELECT 1",
        "password": "hunter2",
        "nested": {
            "api_key": "sk-super-secret",
            "items": [
                {"client_token": "tok-123"},
                "plain",
            ],
        },
        "size": 42,
    }

    trace, secret_values = build_arguments_trace(
        original
    )

    # Non-mutation.
    assert original["password"] == "hunter2"
    assert (
        original["nested"]["api_key"]
        == "sk-super-secret"
    )
    assert (
        original["nested"]["items"][0][
            "client_token"
        ]
        == "tok-123"
    )

    # Secret keys are redacted in the trace.
    assert trace["arguments"]["password"] == "[REDACTED]"
    assert (
        trace["arguments"]["nested"]["api_key"]
        == "[REDACTED]"
    )
    assert (
        trace["arguments"]["nested"]["items"][0][
            "client_token"
        ]
        == "[REDACTED]"
    )

    # Non-secret values pass through untouched.
    assert trace["arguments"]["query"] == "SELECT 1"
    assert trace["arguments"]["size"] == 42

    # Original secret values are collected for scrubbing.
    assert "hunter2" in secret_values
    assert "sk-super-secret" in secret_values
    assert "tok-123" in secret_values

    # Canonical schema.
    assert trace["arguments_truncated"] is False
    assert isinstance(trace["arguments_size_bytes"], int)
    assert trace["arguments_size_bytes"] > 0

    # Over the size limit, arguments become a bounded string.
    big = {"blob": "x" * (MAX_ARGUMENTS_BYTES + 100)}

    big_trace, _ = build_arguments_trace(big)

    assert isinstance(
        big_trace["arguments"],
        str,
    )
    assert big_trace["arguments_truncated"] is True
    assert (
        len(
            big_trace["arguments"].encode(
                "utf-8"
            )
        )
        <= MAX_ARGUMENTS_BYTES
    )
    assert (
        big_trace["arguments_size_bytes"]
        > MAX_ARGUMENTS_BYTES
    )


def test_result_trace_and_error_scrubbing_helpers() -> None:
    """
    Result previews are always strings with size metadata, and
    error messages are scrubbed of known secret values.
    """

    result = json.dumps({"ok": True}) * 10

    trace = build_result_trace(result)

    assert trace["result_preview"] == result
    assert isinstance(trace["result_preview"], str)
    assert trace["result_truncated"] is False
    assert trace["result_type"] == "str"
    assert trace["result_size_bytes"] == len(
        result.encode("utf-8")
    )

    big_result = "y" * (MAX_RESULT_PREVIEW_BYTES + 500)

    big_trace = build_result_trace(big_result)

    assert big_trace["result_truncated"] is True
    assert (
        len(
            big_trace["result_preview"].encode(
                "utf-8"
            )
        )
        <= MAX_RESULT_PREVIEW_BYTES
    )
    # The size metadata describes the full result.
    assert (
        big_trace["result_size_bytes"]
        == MAX_RESULT_PREVIEW_BYTES + 500
    )

    # Non-string results are previewed as strings.
    non_string = build_result_trace({"ok": False})

    assert isinstance(
        non_string["result_preview"],
        str,
    )
    assert non_string["result_type"] == "dict"

    # Error scrubbing.
    scrubbed = scrub_error_message(
        "connection failed with password=hunter2 and sk-super-secret",
        ["hunter2", "sk-super-secret"],
    )

    assert "hunter2" not in scrubbed
    assert "sk-super-secret" not in scrubbed
    assert "[REDACTED]" in scrubbed

    # Console capping.
    long_value = "z" * (CONSOLE_VALUE_CHAR_LIMIT + 50)
    capped = cap_value(long_value)

    assert len(capped) <= CONSOLE_VALUE_CHAR_LIMIT + 3
    assert capped.endswith("...")

    short = cap_value("short")

    assert short == "short"


# ============================================================
# 10. Malformed JSON tool arguments
# ============================================================


@pytest.mark.asyncio
async def test_worker_malformed_json_tool_arguments() -> None:
    """
    Malformed JSON arguments emit a STARTED event carrying
    arguments_raw and arguments_parse_error, then a FAILED event
    with terminal fields only, correlated by tool_call_id. The
    synthetic error tool result still reaches the LLM.
    """

    final_message = ChatCompletionMessage(
        role="assistant",
        content="Recovered from malformed arguments.",
        tool_calls=None,
    )

    worker, sink = make_worker(
        responses=[
            ChatCompletionMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    make_tool_call(
                        "call_bad_001",
                        "echo",
                        '{"query": "oops"',
                    )
                ],
            ),
            final_message,
        ],
        behaviors={"echo": '{"ok": true}'},
    )

    await worker.start(
        query="run the echo tool",
        request_id="req-obs-001",
    )

    await worker.run_step()
    await worker.run_step()

    started_events = events_named(
        sink,
        "tool_call_started",
    )
    failed_events = events_named(
        sink,
        "tool_call_failed",
    )

    assert len(started_events) == 1
    assert len(failed_events) == 1

    started = started_events[0].data
    failed = failed_events[0].data

    # STARTED event carries the raw payload and parse error.
    assert started["tool_call_id"] == "call_bad_001"
    assert started["tool_name"] == "echo"
    assert started["arguments_raw"] == '{"query": "oops"'
    assert "JSONDecodeError" in started[
        "arguments_parse_error"
    ]

    # FAILED event carries terminal fields only.
    assert failed["tool_call_id"] == "call_bad_001"
    assert failed["tool_name"] == "echo"
    assert isinstance(
        failed["duration_seconds"],
        float,
    )
    assert failed["error"] == "JSONDecodeError"
    assert failed["error_message"]

    for absent_key in (
        "arguments_raw",
        "arguments_parse_error",
        "arguments",
        "arguments_truncated",
        "arguments_size_bytes",
    ):
        assert absent_key not in failed

    # The synthetic error tool result still reaches the LLM.
    tool_message = worker.messages[-1]

    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_bad_001"
    assert "Invalid JSON tool arguments" in tool_message[
        "content"
    ]


# ============================================================
# 11. Projection correlation
# ============================================================


def make_event(
    event_name: str,
    data: dict,
) -> RuntimeEvent:
    return RuntimeEvent(
        timestamp=datetime.now(timezone.utc),
        component="generic_worker",
        event=event_name,
        request_id="req-obs-002",
        task_id="task-obs-002",
        data=data,
    )


def test_projection_current_tool_correlates_by_tool_call_id() -> None:
    """
    The projection correlates started/terminal events by
    tool_call_id (parallel-safe) and falls back to legacy
    name-based behavior when tool_call_id is absent.
    """

    # Two tools started, first one completed: second still active.
    events = [
        make_event(
            "tool_call_started",
            {
                "tool_call_id": "call_a",
                "tool_name": "sql_executor",
                "iteration": 1,
            },
        ),
        make_event(
            "tool_call_started",
            {
                "tool_call_id": "call_b",
                "tool_name": "web_search",
                "iteration": 2,
            },
        ),
        make_event(
            "tool_call_completed",
            {
                "tool_call_id": "call_a",
                "tool_name": "sql_executor",
                "iteration": 1,
            },
        ),
    ]

    assert (
        _current_tool(events)
        == "web_search"
    )

    # Completing the second clears the active tool.
    events.append(
        make_event(
            "tool_call_failed",
            {
                "tool_call_id": "call_b",
                "tool_name": "web_search",
                "iteration": 2,
            },
        )
    )

    assert _current_tool(events) is None

    # Legacy fallback: events without tool_call_id still work.
    legacy_events = [
        make_event(
            "tool_call_started",
            {
                "tool_name": "sql_executor",
                "iteration": 1,
            },
        ),
    ]

    assert (
        _current_tool(legacy_events)
        == "sql_executor"
    )

    legacy_events.append(
        make_event(
            "tool_call_completed",
            {
                "tool_name": "sql_executor",
                "iteration": 1,
            },
        )
    )

    assert _current_tool(legacy_events) is None


# ============================================================
# 12. Terminal console bounding
# ============================================================


def test_terminal_sink_caps_value_length(capsys) -> None:
    """
    Terminal rendering caps each value so large tool payloads
    cannot flood the console, while small values pass unchanged.
    """

    sink = TerminalSink()

    long_preview = "x" * 5000

    line = sink._format_data(
        {
            "tool_name": "echo",
            "result_preview": long_preview,
            "error_message": "short failure",
        }
    )

    assert "tool_name=echo" in line
    assert long_preview not in line
    assert (
        'error_message="short failure"'
        in line
    )

    for part in line.split(" "):
        key, _, value = part.partition("=")

        if key == "error_message":
            value = f'"{value}"'

        assert len(value) <= (
            CONSOLE_VALUE_CHAR_LIMIT + 3
        )

    # Full emit path stays readable.
    tracer = RuntimeTracer(sinks=[sink])

    tracer.emit(
        component="generic_worker",
        event="tool_call_completed",
        request_id="req-obs-003",
        task_id="task-obs-003",
        worker_role="task",
        iteration=1,
        tool_call_id="call_big",
        tool_name="echo",
        duration_seconds=0.01,
        **build_result_trace(long_preview),
    )

    captured = capsys.readouterr()

    assert "TOOL_CALL_COMPLETED" in captured.out
    assert "tool_call_id=call_big" in captured.out
    assert long_preview not in captured.out


# ============================================================
# 13. Full result reaches the LLM + error scrubbing
# ============================================================


@pytest.mark.asyncio
async def test_worker_full_result_reaches_llm_and_errors_scrubbed() -> None:
    """
    The complete tool result always reaches the LLM even when the
    event preview is truncated, and tool failure messages are
    scrubbed of secret values before being emitted.
    """

    big_result = json.dumps(
        {
            "ok": True,
            "rows": [
                {"id": index, "data": "y" * 50}
                for index in range(400)
            ],
        }
    )

    secret = "hunter2secret"

    worker, sink = make_worker(
        responses=[
            ChatCompletionMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    make_tool_call(
                        "call_big_001",
                        "big_tool",
                        json.dumps(
                            {
                                "query": "big",
                                "password": secret,
                            }
                        ),
                    )
                ],
            ),
            ChatCompletionMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    make_tool_call(
                        "call_fail_001",
                        "big_tool",
                        json.dumps(
                            {
                                "query": "fail",
                                "password": secret,
                            }
                        ),
                    )
                ],
            ),
            ChatCompletionMessage(
                role="assistant",
                content="Done.",
                tool_calls=None,
            ),
        ],
        behaviors={
            "big_tool": big_result,
        },
    )

    async def failing_call_tool(
        name: str,
        arguments: dict,
    ) -> str:
        raise ValueError(
            "Connection failed using password="
            + secret
        )

    # Second call fails with a secret-bearing error.
    original_call_tool = worker.tools.call_tool

    call_count = {"count": 0}

    async def scripted_call_tool(
        name: str,
        arguments: dict,
    ) -> str:
        call_count["count"] += 1

        if call_count["count"] == 2:
            raise ValueError(
                "Connection failed using password="
                + secret
            )

        return await original_call_tool(
            name,
            arguments,
        )

    worker.tools.call_tool = scripted_call_tool

    await worker.start(
        query="exercise big result and failure",
        request_id="req-obs-004",
    )

    await worker.run_step()
    await worker.run_step()
    await worker.run_step()

    started_events = events_named(
        sink,
        "tool_call_started",
    )
    completed_events = events_named(
        sink,
        "tool_call_completed",
    )
    failed_events = events_named(
        sink,
        "tool_call_failed",
    )

    # Success path: canonical arguments schema on STARTED.
    big_started = started_events[0].data

    assert big_started["tool_call_id"] == "call_big_001"
    assert big_started["arguments"] == {
        "query": "big",
        "password": "[REDACTED]",
    }
    assert big_started["arguments_truncated"] is False
    assert (
        big_started["arguments_size_bytes"]
        > 0
    )

    # Success path: bounded result preview on COMPLETED.
    big_completed = completed_events[0].data

    assert big_completed["tool_call_id"] == "call_big_001"
    assert (
        big_completed["result_truncated"]
        is True
    )
    assert (
        len(
            big_completed[
                "result_preview"
            ].encode("utf-8")
        )
        <= MAX_RESULT_PREVIEW_BYTES
    )
    assert (
        big_completed["result_size_bytes"]
        == len(big_result.encode("utf-8"))
    )
    assert (
        0
        <= big_completed["duration_seconds"]
    )

    # The COMPLETE tool result still reaches the LLM.
    tool_message = worker.messages[3]

    assert tool_message["role"] == "tool"
    assert (
        tool_message["tool_call_id"]
        == "call_big_001"
    )
    assert tool_message["content"] == big_result

    # Failure path: error message is scrubbed.
    assert len(failed_events) == 1

    failed = failed_events[0].data

    assert failed["tool_call_id"] == "call_fail_001"
    assert failed["error"] == "ValueError"
    assert secret not in failed["error_message"]
    assert "[REDACTED]" in failed["error_message"]
    assert isinstance(
        failed["duration_seconds"],
        float,
    )
