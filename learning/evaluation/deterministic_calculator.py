"""Load KernelAI traces into structured evaluation records."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from learning.evaluation.execution import (
    ExecutionRecord,
    TaskExecutionRecord,
    ToolCallRecord,
)
from learning.models import TracePlane


TRACE_ROOT = (
    Path(__file__).parents[1]
    / "data"
    / "traces"
)


def load_execution(
    path: Path,
) -> ExecutionRecord:
    """
    Load one JSONL trace file and convert it into an
    evaluation-ready ExecutionRecord.

    The trace file must contain events belonging to exactly
    one KernelAI Request.
    """

    events = _load_events(path)

    if not events:
        raise ValueError(
            f"Trace file contains no events: {path}"
        )

    return _build_execution(events)


def get_trace_directory(
    plane: TracePlane,
) -> Path:
    """Return the trace directory for one execution plane."""

    return TRACE_ROOT / plane.value


def _load_events(
    path: Path,
) -> list[dict[str, Any]]:
    """Load raw RuntimeEvents from one JSONL trace file."""

    if not path.exists():
        raise FileNotFoundError(
            f"Trace file not found: {path}"
        )

    if not path.is_file():
        raise ValueError(
            f"Trace path is not a file: {path}"
        )

    events: list[dict[str, Any]] = []

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        for line_number, line in enumerate(
            file,
            start=1,
        ):
            line = line.strip()

            if not line:
                continue

            try:
                event = json.loads(line)

            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON in {path} "
                    f"at line {line_number}."
                ) from exc

            if not isinstance(event, dict):
                raise ValueError(
                    f"Trace event at line {line_number} "
                    f"in {path} must be a JSON object."
                )

            events.append(event)

    return events


def _build_execution(
    events: list[dict[str, Any]],
) -> ExecutionRecord:
    """Convert raw RuntimeEvents into one ExecutionRecord."""

    request_ids = {
        event.get("request_id")
        for event in events
        if event.get("request_id") is not None
    }

    if len(request_ids) != 1:
        raise ValueError(
            "Trace must contain events from exactly one request."
        )

    request_id = next(iter(request_ids))

    task_events: dict[
        str,
        list[dict[str, Any]],
    ] = defaultdict(list)

    supervisor_events: list[
        dict[str, Any]
    ] = []

    for event in events:
        task_id = event.get("task_id")

        if task_id is None:
            supervisor_events.append(event)
            continue

        task_events[str(task_id)].append(
            event
        )

    tasks = tuple(
        _build_task(
            task_id=task_id,
            events=current_events,
        )
        for task_id, current_events
        in task_events.items()
    )

    return ExecutionRecord(
        request_id=str(request_id),
        query=_extract_query(events),
        status=_request_status(events),
        final_answer=_extract_final_answer(events),
        duration_seconds=_duration_between(
            events,
            start_event="request_submitted",
            terminal_events={
                "request_completed",
                "request_failed",
                "request_cancelled",
            },
        ),
        tasks=tasks,
        supervisor_llm_calls=_count_supervisor_llm_calls(
            supervisor_events
        ),
        input_tokens=_sum_usage(
            events,
            "input_tokens",
        ),
        output_tokens=_sum_usage(
            events,
            "output_tokens",
        ),
    )


def _build_task(
    task_id: str,
    events: list[dict[str, Any]],
) -> TaskExecutionRecord:
    """Build one Task execution from its RuntimeEvents."""

    return TaskExecutionRecord(
        task_id=task_id,
        description=_extract_task_description(
            events
        ),
        skills=_extract_task_skills(
            events
        ),
        status=_task_status(
            events
        ),
        llm_calls=_count_task_llm_calls(
            events
        ),
        tool_calls=_build_tool_calls(
            events
        ),
        duration_seconds=_duration_between(
            events,
            start_event="task_started",
            terminal_events={
                "task_completed",
                "task_failed",
            },
        ),
    )


def _build_tool_calls(
    events: list[dict[str, Any]],
) -> tuple[ToolCallRecord, ...]:
    """
    Pair tool-start and terminal events using tool_call_id.

    JSONLSink flattens RuntimeEvent.data into the top-level
    JSON object, so tool metadata is read directly from each
    event dictionary.
    """

    started: dict[
        str,
        dict[str, Any],
    ] = {}

    records: list[
        ToolCallRecord
    ] = []

    for event in events:
        event_name = event.get(
            "event"
        )

        tool_call_id = event.get(
            "tool_call_id"
        )

        if not isinstance(
            tool_call_id,
            str,
        ):
            continue

        if event_name == "tool_call_started":
            started[
                tool_call_id
            ] = event

            continue

        if event_name not in {
            "tool_call_completed",
            "tool_call_failed",
        }:
            continue

        start_event = started.get(
            tool_call_id
        )

        if start_event is None:
            continue

        records.append(
            ToolCallRecord(
                tool_call_id=tool_call_id,
                tool_name=str(
                    event.get(
                        "tool_name",
                        start_event.get(
                            "tool_name",
                            "",
                        ),
                    )
                ),
                arguments=start_event.get(
                    "arguments",
                    start_event.get(
                        "arguments_raw",
                    ),
                ),
                result=event.get(
                    "result_preview"
                ),
                success=(
                    event_name
                    == "tool_call_completed"
                ),
                duration_seconds=_optional_float(
                    event.get(
                        "duration_seconds"
                    )
                ),
                iteration=_optional_int(
                    event.get(
                        "iteration"
                    )
                ),
                error=_optional_string(
                    event.get(
                        "error_message"
                    )
                ),
            )
        )

    return tuple(records)


def _extract_query(
    events: list[dict[str, Any]],
) -> str:
    """
    Extract the original Request query.

    Current runtime events do not persist the Request query
    themselves, so this returns an empty string until query
    provenance is added to the persisted trace contract.
    """

    for event in events:
        query = event.get(
            "query"
        )

        if isinstance(query, str):
            return query

    return ""


def _extract_final_answer(
    events: list[dict[str, Any]],
) -> Any | None:
    """
    Extract the final Request result when present in the trace.

    The current orchestrator REQUEST_COMPLETED event does not
    include the result, so this may legitimately return None.
    """

    for event in reversed(events):
        if (
            event.get("event")
            != "request_completed"
        ):
            continue

        return event.get(
            "result"
        )

    return None


def _extract_task_description(
    events: list[dict[str, Any]],
) -> str:
    """Extract the delegated Task instruction."""

    for event in events:
        if (
            event.get("event")
            != "task_delegated"
        ):
            continue

        for key in (
            "task",
            "query",
            "description",
        ):
            value = event.get(
                key
            )

            if isinstance(
                value,
                str,
            ):
                return value

    return ""


def _extract_task_skills(
    events: list[dict[str, Any]],
) -> tuple[str, ...]:
    """Extract skills assigned during TASK_DELEGATED."""

    for event in events:
        if (
            event.get("event")
            != "task_delegated"
        ):
            continue

        skills = event.get(
            "skills"
        )

        if isinstance(
            skills,
            list,
        ):
            return tuple(
                str(skill)
                for skill in skills
            )

    return ()


def _request_status(
    events: list[dict[str, Any]],
) -> str:
    """Determine terminal Request status."""

    if _has_event(
        events,
        "request_failed",
    ):
        return "failed"

    if _has_event(
        events,
        "request_cancelled",
    ):
        return "cancelled"

    if _has_event(
        events,
        "request_completed",
    ):
        return "completed"

    if _has_event(
        events,
        "request_admitted",
    ):
        return "running"

    if _has_event(
        events,
        "request_submitted",
    ):
        return "submitted"

    return "unknown"


def _task_status(
    events: list[dict[str, Any]],
) -> str:
    """Determine terminal Task status."""

    if _has_event(
        events,
        "task_failed",
    ):
        return "failed"

    if _has_event(
        events,
        "task_completed",
    ):
        return "completed"

    if _has_event(
        events,
        "task_started",
    ):
        return "running"

    if _has_event(
        events,
        "task_waiting",
    ):
        return "waiting"

    if _has_event(
        events,
        "task_delegated",
    ):
        return "delegated"

    return "unknown"


def _count_supervisor_llm_calls(
    events: list[dict[str, Any]],
) -> int:
    """
    Count completed LLM calls made by the Supervisor worker.
    """

    return sum(
        1
        for event in events
        if (
            event.get("component")
            == "generic_worker"
            and event.get("event")
            == "llm_call_completed"
            and event.get("worker_role")
            == "supervisor"
        )
    )


def _count_task_llm_calls(
    events: list[dict[str, Any]],
) -> int:
    """
    Count completed LLM calls made by one Task Worker.
    """

    return sum(
        1
        for event in events
        if (
            event.get("component")
            == "generic_worker"
            and event.get("event")
            == "llm_call_completed"
            and event.get("worker_role")
            == "task"
        )
    )


def _duration_between(
    events: list[dict[str, Any]],
    start_event: str,
    terminal_events: set[str],
) -> float | None:
    """
    Calculate duration between lifecycle events using the
    ISO-8601 timestamps persisted by JSONLSink.
    """

    start_timestamp: datetime | None = None
    end_timestamp: datetime | None = None

    for event in events:
        event_name = event.get(
            "event"
        )

        if (
            event_name == start_event
            and start_timestamp is None
        ):
            start_timestamp = _parse_timestamp(
                event.get(
                    "timestamp"
                )
            )

        if event_name in terminal_events:
            timestamp = _parse_timestamp(
                event.get(
                    "timestamp"
                )
            )

            if timestamp is not None:
                end_timestamp = timestamp

    if (
        start_timestamp is None
        or end_timestamp is None
    ):
        return None

    duration = (
        end_timestamp
        - start_timestamp
    ).total_seconds()

    if duration < 0:
        return None

    return duration


def _parse_timestamp(
    value: Any,
) -> datetime | None:
    """Parse one persisted ISO-8601 RuntimeEvent timestamp."""

    if not isinstance(
        value,
        str,
    ):
        return None

    try:
        return datetime.fromisoformat(
            value
        )

    except ValueError:
        return None


def _sum_usage(
    events: list[dict[str, Any]],
    key: str,
) -> int | None:
    """
    Sum token usage when token fields are present.

    Missing usage remains None rather than being represented
    as zero.
    """

    total = 0
    found = False

    for event in events:
        value = event.get(
            key
        )

        if (
            isinstance(value, int)
            and not isinstance(value, bool)
        ):
            total += value
            found = True

    if not found:
        return None

    return total


def _count_events(
    events: list[dict[str, Any]],
    event_name: str,
) -> int:
    """Count occurrences of one RuntimeEvent type."""

    return sum(
        1
        for event in events
        if event.get(
            "event"
        ) == event_name
    )


def _has_event(
    events: list[dict[str, Any]],
    event_name: str,
) -> bool:
    """Return whether an event type exists."""

    return any(
        event.get(
            "event"
        ) == event_name
        for event in events
    )


def _optional_float(
    value: Any,
) -> float | None:
    if (
        isinstance(
            value,
            (int, float),
        )
        and not isinstance(
            value,
            bool,
        )
    ):
        return float(value)

    return None


def _optional_int(
    value: Any,
) -> int | None:
    if (
        isinstance(
            value,
            int,
        )
        and not isinstance(
            value,
            bool,
        )
    ):
        return value

    return None


def _optional_string(
    value: Any,
) -> str | None:
    if isinstance(
        value,
        str,
    ):
        return value

    return None