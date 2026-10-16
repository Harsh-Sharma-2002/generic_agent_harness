"""Projection layer for KernelAI runtime observability."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from observability.event import RuntimeEvent


# ============================================================
# Public projection
# ============================================================


def project_request(
    events: list[RuntimeEvent],
) -> dict[str, Any]:
    """
    Project RuntimeEvents for one Request into frontend-friendly
    lifecycle state.

    The input events remain the source of truth. This function does
    not mutate them or maintain persistent projection state.
    """

    if not events:
        raise ValueError(
            "Cannot project an empty Request event stream."
        )

    ordered_events = sorted(
        events,
        key=lambda event: event.timestamp,
    )

    request_ids = {
        event.request_id
        for event in ordered_events
        if event.request_id is not None
    }

    if len(request_ids) != 1:
        raise ValueError(
            "Request projection requires events belonging "
            "to exactly one Request."
        )

    request_id = next(
        iter(request_ids)
    )

    request = _project_request_lifecycle(
        ordered_events
    )

    supervisor = _project_supervisor(
        ordered_events
    )

    tasks = _project_tasks(
        ordered_events
    )

    counts = _task_counts(
        tasks
    )

    activity = _project_activity(
        events=ordered_events,
        request=request,
        supervisor=supervisor,
        tasks=tasks,
        counts=counts,
    )

    metrics = _project_metrics(
        ordered_events
    )

    errors = _project_errors(
        ordered_events
    )

    return {
        "request_id": request_id,
        "status": request["status"],
        "activity": activity,
        "timing": request["timing"],
        "supervisor": supervisor,
        "task_count": len(tasks),
        "active_tasks": counts["active"],
        "waiting_tasks": counts["waiting"],
        "completed_tasks": counts["completed"],
        "failed_tasks": counts["failed"],
        "tasks": tasks,
        "metrics": metrics,
        "errors": errors,
    }


# ============================================================
# Request lifecycle
# ============================================================


def _project_request_lifecycle(
    events: list[RuntimeEvent],
) -> dict[str, Any]:

    submitted = _first_event(
        events,
        component="orchestrator",
        event_name="request_submitted",
    )

    admitted = _first_event(
        events,
        component="orchestrator",
        event_name="request_admitted",
    )

    completed = _last_event(
        events,
        component="orchestrator",
        event_name="request_completed",
    )

    failed = _last_event(
        events,
        component="orchestrator",
        event_name="request_failed",
    )

    cancelled = _last_event(
        events,
        component="orchestrator",
        event_name="request_cancelled",
    )

    if failed is not None:
        status = "failed"

    elif cancelled is not None:
        status = "cancelled"

    elif completed is not None:
        status = "completed"

    elif admitted is not None:
        status = "running"

    elif submitted is not None:
        status = "submitted"

    else:
        status = "unknown"

    submitted_at = (
        submitted.timestamp
        if submitted is not None
        else None
    )

    admitted_at = (
        admitted.timestamp
        if admitted is not None
        else None
    )

    terminal_event = (
        failed
        or cancelled
        or completed
    )

    completed_at = (
        terminal_event.timestamp
        if terminal_event is not None
        else None
    )

    return {
        "status": status,
        "timing": {
            "submitted_at": _iso(
                submitted_at
            ),
            "admitted_at": _iso(
                admitted_at
            ),
            "completed_at": _iso(
                completed_at
            ),
            "queue_seconds": _duration(
                submitted_at,
                admitted_at,
            ),
            "execution_seconds": _duration(
                admitted_at,
                completed_at,
            ),
            "total_seconds": _duration(
                submitted_at,
                completed_at,
            ),
        },
    }


# ============================================================
# Supervisor
# ============================================================


def _project_supervisor(
    events: list[RuntimeEvent],
) -> dict[str, Any]:

    started = _first_event(
        events,
        component="supervisor",
        event_name="supervisor_started",
    )

    completed = _last_event(
        events,
        component="supervisor",
        event_name="supervisor_completed",
    )

    failed = _last_event(
        events,
        component="supervisor",
        event_name="supervisor_failed",
    )

    if failed is not None:
        status = "failed"

    elif completed is not None:
        status = "completed"

    elif started is not None:
        status = "running"

    else:
        status = "waiting"

    started_at = (
        started.timestamp
        if started is not None
        else None
    )

    terminal_event = (
        failed
        or completed
    )

    completed_at = (
        terminal_event.timestamp
        if terminal_event is not None
        else None
    )

    return {
        "status": status,
        "started_at": _iso(
            started_at
        ),
        "completed_at": _iso(
            completed_at
        ),
        "duration_seconds": _duration(
            started_at,
            completed_at,
        ),
    }


# ============================================================
# Tasks
# ============================================================


def _project_tasks(
    events: list[RuntimeEvent],
) -> list[dict[str, Any]]:

    task_ids: list[str] = []

    for event in events:
        if (
            event.task_id is not None
            and event.task_id not in task_ids
        ):
            task_ids.append(
                event.task_id
            )

    tasks = [
        _project_task(
            events,
            task_id,
        )
        for task_id in task_ids
    ]

    tasks.sort(
        key=lambda task: (
            task["delegated_at"]
            or ""
        )
    )

    return tasks


def _project_task(
    events: list[RuntimeEvent],
    task_id: str,
) -> dict[str, Any]:

    task_events = [
        event
        for event in events
        if event.task_id == task_id
    ]

    delegated = _first_event(
        task_events,
        component="supervisor",
        event_name="task_delegated",
    )

    waiting = _first_event(
        task_events,
        component="supervisor",
        event_name="task_waiting",
    )

    started = _first_event(
        task_events,
        component="supervisor",
        event_name="task_started",
    )

    completed = _last_event(
        task_events,
        component="task_executor",
        event_name="task_completed",
    )

    failed = _last_event(
        task_events,
        component="task_executor",
        event_name="task_failed",
    )

    if failed is not None:
        status = "failed"

    elif completed is not None:
        status = "completed"

    elif started is not None:
        status = "running"

    elif waiting is not None:
        status = "waiting"

    elif delegated is not None:
        status = "delegated"

    else:
        status = "unknown"

    delegated_at = (
        delegated.timestamp
        if delegated is not None
        else None
    )

    waiting_at = (
        waiting.timestamp
        if waiting is not None
        else None
    )

    started_at = (
        started.timestamp
        if started is not None
        else None
    )

    terminal_event = (
        failed
        or completed
    )

    completed_at = (
        terminal_event.timestamp
        if terminal_event is not None
        else None
    )

    description = None
    skills: list[str] = []

    if delegated is not None:
        description = delegated.data.get(
            "task"
        )

        delegated_skills = delegated.data.get(
            "skills",
            [],
        )

        if isinstance(
            delegated_skills,
            list,
        ):
            skills = [
                str(skill)
                for skill in delegated_skills
            ]

    current_tool = _current_tool(
        task_events
    )

    worker = _project_task_worker(
        task_events
    )

    return {
        "task_id": task_id,
        "description": description,
        "skills": skills,
        "status": status,
        "current_tool": current_tool,
        "delegated_at": _iso(
            delegated_at
        ),
        "waiting_at": _iso(
            waiting_at
        ),
        "started_at": _iso(
            started_at
        ),
        "completed_at": _iso(
            completed_at
        ),
        "queue_seconds": _duration(
            waiting_at or delegated_at,
            started_at,
        ),
        "execution_seconds": _duration(
            started_at,
            completed_at,
        ),
        "worker": worker,
    }


# ============================================================
# Task Worker
# ============================================================


def _project_task_worker(
    events: list[RuntimeEvent],
) -> dict[str, Any]:

    worker_events = [
        event
        for event in events
        if (
            event.component
            == "generic_worker"
            and event.data.get(
                "worker_role"
            )
            == "task"
        )
    ]

    started = _first_event(
        worker_events,
        component="generic_worker",
        event_name="worker_started",
    )

    completed = _last_event(
        worker_events,
        component="generic_worker",
        event_name="worker_completed",
    )

    failed = _last_event(
        worker_events,
        component="generic_worker",
        event_name="worker_failed",
    )

    if failed is not None:
        status = "failed"

    elif completed is not None:
        status = "completed"

    elif started is not None:
        status = "running"

    else:
        status = "waiting"

    started_at = (
        started.timestamp
        if started is not None
        else None
    )

    terminal_event = (
        failed
        or completed
    )

    completed_at = (
        terminal_event.timestamp
        if terminal_event is not None
        else None
    )

    return {
        "status": status,
        "started_at": _iso(
            started_at
        ),
        "completed_at": _iso(
            completed_at
        ),
        "duration_seconds": _duration(
            started_at,
            completed_at,
        ),
        "llm_calls": _count_events(
            worker_events,
            component="generic_worker",
            event_name="llm_call_started",
        ),
        "tool_calls": _count_events(
            worker_events,
            component="generic_worker",
            event_name="tool_call_started",
        ),
    }


# ============================================================
# Task counts
# ============================================================


def _task_counts(
    tasks: list[dict[str, Any]],
) -> dict[str, int]:

    counts = {
        "active": 0,
        "waiting": 0,
        "completed": 0,
        "failed": 0,
    }

    for task in tasks:
        status = task[
            "status"
        ]

        if status == "running":
            counts["active"] += 1

        elif status in {
            "delegated",
            "waiting",
        }:
            counts["waiting"] += 1

        elif status == "completed":
            counts["completed"] += 1

        elif status == "failed":
            counts["failed"] += 1

    return counts


# ============================================================
# Current activity
# ============================================================


def _project_activity(
    *,
    events: list[RuntimeEvent],
    request: dict[str, Any],
    supervisor: dict[str, Any],
    tasks: list[dict[str, Any]],
    counts: dict[str, int],
) -> str:

    request_status = request[
        "status"
    ]

    if request_status == "completed":
        return "Completed"

    if request_status == "failed":
        return "Request failed"

    if request_status == "cancelled":
        return "Request cancelled"

    if request_status == "submitted":
        return "Waiting for execution"

    # Prefer concrete active tool activity.

    active_tools = [
        task["current_tool"]
        for task in tasks
        if task["current_tool"] is not None
    ]

    if active_tools:

        if "web_search" in active_tools:
            return "Searching the web"

        if "sql_executor" in active_tools:
            return "Querying the database"

        tool_name = active_tools[
            0
        ]

        return (
            f"Using {tool_name}"
        )

    # Tasks are actively executing.

    if counts["active"] > 0:
        return (
            f"Running "
            f"{counts['active']} "
            f"of {len(tasks)} tasks"
        )

    # Tasks exist but are waiting for capacity.

    if counts["waiting"] > 0:
        return (
            f"{counts['waiting']} "
            f"tasks waiting"
        )

    # Tasks completed and Supervisor is synthesizing.

    if (
        tasks
        and counts["completed"]
        + counts["failed"]
        == len(tasks)
        and supervisor["status"]
        == "running"
    ):
        return "Preparing final response"

    # Supervisor exists but has not delegated anything yet.

    if supervisor["status"] == "running":
        return "Planning request"

    return "Working"


# ============================================================
# Runtime metrics
# ============================================================


def _project_metrics(
    events: list[RuntimeEvent],
) -> dict[str, int]:

    worker_events = [
        event
        for event in events
        if event.component
        == "generic_worker"
    ]

    return {
        "llm_calls": _count_events(
            worker_events,
            component="generic_worker",
            event_name="llm_call_started",
        ),
        "tool_calls": _count_events(
            worker_events,
            component="generic_worker",
            event_name="tool_call_started",
        ),
        "tool_failures": _count_events(
            worker_events,
            component="generic_worker",
            event_name="tool_call_failed",
        ),
        "llm_failures": _count_events(
            worker_events,
            component="generic_worker",
            event_name="llm_call_failed",
        ),
    }


# ============================================================
# Errors
# ============================================================


def _project_errors(
    events: list[RuntimeEvent],
) -> list[dict[str, Any]]:

    errors: list[
        dict[str, Any]
    ] = []

    for event in events:

        if not (
            event.event.endswith(
                "_failed"
            )
            or event.data.get(
                "error"
            )
        ):
            continue

        errors.append(
            {
                "timestamp": (
                    event.timestamp.isoformat()
                ),
                "component": (
                    event.component
                ),
                "event": (
                    event.event
                ),
                "task_id": (
                    event.task_id
                ),
                "error": (
                    event.data.get(
                        "error"
                    )
                ),
                "error_message": (
                    event.data.get(
                        "error_message"
                    )
                ),
            }
        )

    return errors


# ============================================================
# Current tool
# ============================================================


def _current_tool(
    events: list[RuntimeEvent],
) -> str | None:
    """
    Return the currently executing tool for one Task, if any.

    A tool is active when its latest relevant lifecycle event is
    TOOL_CALL_STARTED without a later completion/failure for that
    same tool invocation.

    Correlation uses tool_call_id when present. Events without a
    tool_call_id fall back to the legacy name-based behavior where
    any terminal event clears the active tool.
    """

    active_tool: str | None = None
    active_tool_id: str | None = None

    for event in events:

        if event.component != "generic_worker":
            continue

        if event.event == "tool_call_started":
            tool_name = event.data.get(
                "tool_name"
            )

            tool_call_id = event.data.get(
                "tool_call_id"
            )

            if tool_name is not None:
                active_tool = str(
                    tool_name
                )

            active_tool_id = (
                str(tool_call_id)
                if tool_call_id is not None
                else None
            )

        elif event.event in {
            "tool_call_completed",
            "tool_call_failed",
        }:
            terminal_id = event.data.get(
                "tool_call_id"
            )

            if (
                terminal_id is not None
                and active_tool_id is not None
            ):
                if str(terminal_id) == active_tool_id:
                    active_tool = None
                    active_tool_id = None

            else:
                # Legacy events carry no tool_call_id:
                # any terminal event clears the tool.

                active_tool = None
                active_tool_id = None

    return active_tool


# ============================================================
# Event helpers
# ============================================================


def _first_event(
    events: list[RuntimeEvent],
    *,
    component: str,
    event_name: str,
) -> RuntimeEvent | None:

    for event in events:
        if (
            event.component == component
            and event.event == event_name
        ):
            return event

    return None


def _last_event(
    events: list[RuntimeEvent],
    *,
    component: str,
    event_name: str,
) -> RuntimeEvent | None:

    for event in reversed(
        events
    ):
        if (
            event.component == component
            and event.event == event_name
        ):
            return event

    return None


def _count_events(
    events: list[RuntimeEvent],
    *,
    component: str,
    event_name: str,
) -> int:

    return sum(
        1
        for event in events
        if (
            event.component == component
            and event.event == event_name
        )
    )


# ============================================================
# Timing helpers
# ============================================================


def _duration(
    start: datetime | None,
    end: datetime | None,
) -> float | None:

    if (
        start is None
        or end is None
    ):
        return None

    return (
        end - start
    ).total_seconds()


def _iso(
    timestamp: datetime | None,
) -> str | None:

    if timestamp is None:
        return None

    return timestamp.isoformat()

