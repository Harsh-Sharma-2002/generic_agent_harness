"""KernelAI observability dashboard."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
import streamlit as st


# ==========================================================
# Configuration
# ==========================================================

API_BASE_URL = os.getenv(
    "API_BASE_URL",
    "http://127.0.0.1:8000",
)

POLL_INTERVAL = 1.0

ACTIVE_STATUSES = {
    "submitted",
    "running",
}


# ==========================================================
# Page Configuration
# ==========================================================

st.set_page_config(
    page_title="KernelAI Observability",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ==========================================================
# Session State
# ==========================================================

if "dashboard_request_id" not in st.session_state:
    st.session_state.dashboard_request_id = None


# ==========================================================
# API Helpers
# ==========================================================

def fetch_dashboard_requests() -> list[dict[str, Any]]:
    """Fetch all Requests known to the observability API."""

    response = httpx.get(
        f"{API_BASE_URL}/dashboard/requests",
        timeout=5.0,
    )

    response.raise_for_status()

    return response.json()


def fetch_dashboard_request(
    request_id: str,
) -> dict[str, Any]:
    """Fetch the observability projection for one Request."""

    response = httpx.get(
        f"{API_BASE_URL}/dashboard/requests/{request_id}",
        timeout=5.0,
    )

    response.raise_for_status()

    return response.json()


def fetch_request_events(
    request_id: str,
) -> list[dict[str, Any]]:
    """Fetch raw RuntimeEvents for one Request."""

    response = httpx.get(
        f"{API_BASE_URL}/dashboard/requests/{request_id}/events",
        timeout=5.0,
    )

    response.raise_for_status()

    return response.json()


def fetch_task_events(
    task_id: str,
) -> list[dict[str, Any]]:
    """Fetch raw RuntimeEvents for one Task."""

    response = httpx.get(
        f"{API_BASE_URL}/dashboard/tasks/{task_id}/events",
        timeout=5.0,
    )

    response.raise_for_status()

    return response.json()


# ==========================================================
# Formatting Helpers
# ==========================================================

def short_query(
    query: str,
    limit: int = 60,
) -> str:
    """Produce a compact Request label."""

    query = " ".join(
        query.split()
    )

    if len(query) <= limit:
        return query

    return query[:limit] + "..."


def short_id(
    value: str | None,
) -> str:
    """Shorten internal IDs for dashboard display."""

    if not value:
        return "—"

    if len(value) <= 16:
        return value

    return value[:12] + "..."


def status_icon(
    status: str,
) -> str:
    """Return a compact status icon."""

    return {
        "submitted": "○",
        "waiting": "○",
        "delegated": "○",
        "running": "●",
        "completed": "✓",
        "failed": "✕",
        "cancelled": "–",
    }.get(
        status,
        "•",
    )


def format_seconds(
    value: float | None,
) -> str:
    """Format a duration for dashboard display."""

    if value is None:
        return "—"

    if value < 0.001:
        return f"{value * 1_000_000:.0f} µs"

    if value < 1:
        return f"{value * 1000:.1f} ms"

    if value < 60:
        return f"{value:.2f} s"

    minutes = int(
        value // 60
    )

    seconds = (
        value % 60
    )

    return (
        f"{minutes}m {seconds:.1f}s"
    )


def format_timestamp(
    timestamp: str | None,
) -> str:
    """Produce a compact timestamp for event display."""

    if not timestamp:
        return "—"

    if "T" in timestamp:
        timestamp = timestamp.split(
            "T",
            1,
        )[1]

    if "+" in timestamp:
        timestamp = timestamp.split(
            "+",
            1,
        )[0]

    return timestamp


def render_event(
    event: dict[str, Any],
) -> None:
    """Render one raw runtime event."""

    timestamp = format_timestamp(
        event.get("timestamp")
    )

    component = str(
        event.get(
            "component",
            "unknown",
        )
    ).upper()

    event_name = str(
        event.get(
            "event",
            "unknown",
        )
    ).upper()

    task_id = event.get(
        "task_id"
    )

    line = (
        f"{timestamp}  "
        f"{component:<16} "
        f"{event_name}"
    )

    if task_id:
        line += (
            f"  {short_id(task_id)}"
        )

    st.code(
        line,
        language=None,
    )


# ==========================================================
# Sidebar
# ==========================================================

with st.sidebar:
    st.header(
        "KernelAI"
    )

    st.caption(
        "Navigation"
    )

    navigation_columns = st.columns(
        2
    )

    with navigation_columns[0]:
        if st.button(
            "💬 Chat",
            use_container_width=True,
        ):
            st.switch_page(
                "app.py"
            )

    with navigation_columns[1]:
        st.button(
            "📊 Traces",
            use_container_width=True,
            disabled=True,
        )

    st.divider()

    st.caption(
        "Observability"
    )

    st.markdown(
        """
        Request lifecycle, task execution,
        timings, LLM calls, tool calls and
        runtime failures.
        """
    )


# ==========================================================
# Header
# ==========================================================

st.title(
    "Observability"
)

st.caption(
    "Live KernelAI request traces and execution lifecycle."
)


# ==========================================================
# Fetch Request List
# ==========================================================

try:
    dashboard_requests = (
        fetch_dashboard_requests()
    )

except httpx.HTTPError as exc:
    st.error(
        "Could not connect to the KernelAI observability API."
    )

    st.exception(
        exc
    )

    st.stop()


# ==========================================================
# Empty Dashboard
# ==========================================================

if not dashboard_requests:
    st.info(
        "No KernelAI requests have been submitted yet."
    )

    st.stop()


# ==========================================================
# Request Selector
# ==========================================================

request_lookup = {
    record["request_id"]: record
    for record in dashboard_requests
}

available_request_ids = list(
    request_lookup
)

current_request_id = (
    st.session_state.dashboard_request_id
)

if current_request_id not in request_lookup:
    current_request_id = (
        available_request_ids[0]
    )

    st.session_state.dashboard_request_id = (
        current_request_id
    )


def request_option_label(
    request_id: str,
) -> str:
    """Render one Request selector label."""

    record = request_lookup[
        request_id
    ]

    return (
        f"{status_icon(record['status'])} "
        f"{short_query(record['query'])}"
    )


selected_request_id = st.selectbox(
    "Request",
    options=available_request_ids,
    index=available_request_ids.index(
        current_request_id
    ),
    format_func=request_option_label,
)

if (
    selected_request_id
    != st.session_state.dashboard_request_id
):
    st.session_state.dashboard_request_id = (
        selected_request_id
    )

    st.rerun()


# ==========================================================
# Fetch Selected Request
# ==========================================================

try:
    projection = fetch_dashboard_request(
        selected_request_id
    )

except httpx.HTTPError as exc:
    st.error(
        "Could not load Request observability data."
    )

    st.exception(
        exc
    )

    st.stop()


request_record = request_lookup[
    selected_request_id
]


# ==========================================================
# Request Header
# ==========================================================

st.subheader(
    request_record[
        "query"
    ]
)

status = projection[
    "status"
]

activity = projection[
    "activity"
]

st.caption(
    (
        f"{status_icon(status)} "
        f"{status.title()} · "
        f"{activity}"
    )
)


# ==========================================================
# Request Metrics
# ==========================================================

timing = projection[
    "timing"
]

metrics = projection[
    "metrics"
]

metric_columns = st.columns(
    6
)

with metric_columns[0]:
    st.metric(
        "Total",
        format_seconds(
            timing.get(
                "total_seconds"
            )
        ),
    )

with metric_columns[1]:
    st.metric(
        "Queue",
        format_seconds(
            timing.get(
                "queue_seconds"
            )
        ),
    )

with metric_columns[2]:
    st.metric(
        "Execution",
        format_seconds(
            timing.get(
                "execution_seconds"
            )
        ),
    )

with metric_columns[3]:
    st.metric(
        "Tasks",
        projection[
            "task_count"
        ],
    )

with metric_columns[4]:
    st.metric(
        "LLM calls",
        metrics[
            "llm_calls"
        ],
    )

with metric_columns[5]:
    st.metric(
        "Tool calls",
        metrics[
            "tool_calls"
        ],
    )


# ==========================================================
# Task Summary
# ==========================================================

task_summary_columns = st.columns(
    4
)

with task_summary_columns[0]:
    st.metric(
        "Running",
        projection[
            "active_tasks"
        ],
    )

with task_summary_columns[1]:
    st.metric(
        "Waiting",
        projection[
            "waiting_tasks"
        ],
    )

with task_summary_columns[2]:
    st.metric(
        "Completed",
        projection[
            "completed_tasks"
        ],
    )

with task_summary_columns[3]:
    st.metric(
        "Failed",
        projection[
            "failed_tasks"
        ],
    )

st.divider()


# ==========================================================
# Supervisor
# ==========================================================

st.subheader(
    "Supervisor"
)

supervisor = projection[
    "supervisor"
]

supervisor_columns = st.columns(
    4
)

with supervisor_columns[0]:
    st.metric(
        "Status",
        supervisor[
            "status"
        ].title(),
    )

with supervisor_columns[1]:
    st.metric(
        "Duration",
        format_seconds(
            supervisor.get(
                "duration_seconds"
            )
        ),
    )

with supervisor_columns[2]:
    st.metric(
        "Started",
        format_timestamp(
            supervisor.get(
                "started_at"
            )
        ),
    )

with supervisor_columns[3]:
    st.metric(
        "Completed",
        format_timestamp(
            supervisor.get(
                "completed_at"
            )
        ),
    )

st.divider()


# ==========================================================
# Tasks
# ==========================================================

st.subheader(
    "Tasks"
)

tasks = projection.get(
    "tasks",
    [],
)

if not tasks:
    st.caption(
        "No Tasks have been delegated yet."
    )


for index, task in enumerate(
    tasks,
    start=1,
):
    task_status = task[
        "status"
    ]

    description = (
        task.get(
            "description"
        )
        or task["task_id"]
    )

    label = (
        f"{status_icon(task_status)} "
        f"Task {index} · "
        f"{description}"
    )

    with st.expander(
        label,
        expanded=(
            task_status
            in {
                "running",
                "failed",
            }
        ),
    ):
        skills = task.get(
            "skills",
            [],
        )

        if skills:
            st.caption(
                "Skills: "
                + ", ".join(
                    skills
                )
            )

        task_columns = st.columns(
            4
        )

        with task_columns[0]:
            st.metric(
                "Status",
                task_status.title(),
            )

        with task_columns[1]:
            st.metric(
                "Queue",
                format_seconds(
                    task.get(
                        "queue_seconds"
                    )
                ),
            )

        with task_columns[2]:
            st.metric(
                "Execution",
                format_seconds(
                    task.get(
                        "execution_seconds"
                    )
                ),
            )

        with task_columns[3]:
            current_tool = (
                task.get(
                    "current_tool"
                )
            )

            st.metric(
                "Current tool",
                current_tool or "—",
            )

        worker = task.get(
            "worker",
            {},
        )

        worker_columns = st.columns(
            3
        )

        with worker_columns[0]:
            st.metric(
                "Worker",
                worker.get(
                    "status",
                    "unknown",
                ).title(),
            )

        with worker_columns[1]:
            st.metric(
                "LLM calls",
                worker.get(
                    "llm_calls",
                    0,
                ),
            )

        with worker_columns[2]:
            st.metric(
                "Tool calls",
                worker.get(
                    "tool_calls",
                    0,
                ),
            )

        st.caption(
            (
                "Task ID: "
                f"{short_id(task['task_id'])}"
            )
        )

        if st.checkbox(
            "Show task events",
            key=(
                "task-events-"
                f"{task['task_id']}"
            ),
        ):
            try:
                task_events = (
                    fetch_task_events(
                        task[
                            "task_id"
                        ]
                    )
                )

            except httpx.HTTPError:
                st.error(
                    "Could not load Task events."
                )

            else:
                for event in task_events:
                    render_event(
                        event
                    )


# ==========================================================
# Errors
# ==========================================================

errors = projection.get(
    "errors",
    [],
)

if errors:
    st.divider()

    st.subheader(
        "Errors"
    )

    for error in errors:
        with st.expander(
            (
                "✕ "
                f"{error['component']} · "
                f"{error['event']}"
            )
        ):
            st.write(
                error.get(
                    "error"
                )
                or "Unknown error"
            )

            if error.get(
                "error_message"
            ):
                st.code(
                    error[
                        "error_message"
                    ],
                    language=None,
                )


# ==========================================================
# Raw Request Timeline
# ==========================================================

st.divider()

st.subheader(
    "Execution Timeline"
)

show_events = st.toggle(
    "Show raw events",
    value=False,
)

if show_events:
    try:
        events = fetch_request_events(
            selected_request_id
        )

    except httpx.HTTPError as exc:
        st.error(
            "Could not load Request events."
        )

        st.exception(
            exc
        )

    else:
        for event in events:
            render_event(
                event
            )


# ==========================================================
# Live Refresh
# ==========================================================

if status in ACTIVE_STATUSES:
    time.sleep(
        POLL_INTERVAL
    )

    st.rerun()