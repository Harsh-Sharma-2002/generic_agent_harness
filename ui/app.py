# ui/app.py

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
    page_title="KernelAI",
    page_icon="🤖",
    layout="centered",
    initial_sidebar_state="expanded",
)


# ==========================================================
# Session State
# ==========================================================

if "requests" not in st.session_state:
    st.session_state.requests = {}

if "request_order" not in st.session_state:
    st.session_state.request_order = []

if "selected_request_id" not in st.session_state:
    st.session_state.selected_request_id = None


# ==========================================================
# API Helpers
# ==========================================================


def submit_request(
    query: str,
) -> dict[str, Any]:
    """
    Submit one Request to KernelAI.
    """

    response = httpx.post(
        f"{API_BASE_URL}/requests",
        json={
            "query": query,
        },
        timeout=10.0,
    )

    response.raise_for_status()

    return response.json()


def fetch_request(
    request_id: str,
) -> dict[str, Any] | None:
    """
    Fetch API-facing Request state.
    """

    try:
        response = httpx.get(
            f"{API_BASE_URL}/requests/{request_id}",
            timeout=5.0,
        )

    except httpx.HTTPError:
        return None

    if response.status_code == 404:
        return None

    response.raise_for_status()

    return response.json()


def fetch_activity(
    request_id: str,
) -> dict[str, Any] | None:
    """
    Fetch projected live observability state for one Request.
    """

    try:
        response = httpx.get(
            (
                f"{API_BASE_URL}"
                f"/requests/{request_id}/activity"
            ),
            timeout=5.0,
        )

    except httpx.HTTPError:
        return None

    if response.status_code == 404:
        return None

    response.raise_for_status()

    return response.json()


# ==========================================================
# Request Management
# ==========================================================


def create_request(
    query: str,
) -> None:
    """
    Submit one new Request and select it.
    """

    record = submit_request(
        query
    )

    request_id = record[
        "request_id"
    ]

    st.session_state.requests[
        request_id
    ] = record

    st.session_state.request_order.insert(
        0,
        request_id,
    )

    st.session_state.selected_request_id = (
        request_id
    )


def refresh_active_requests() -> bool:
    """
    Refresh all active Request records.

    Returns True while at least one Request remains active.
    """

    has_active = False

    for request_id in list(
        st.session_state.request_order
    ):
        record = st.session_state.requests.get(
            request_id
        )

        if record is None:
            continue

        if record["status"] not in ACTIVE_STATUSES:
            continue

        updated = fetch_request(
            request_id
        )

        if updated is not None:
            st.session_state.requests[
                request_id
            ] = updated

            record = updated

        if record["status"] in ACTIVE_STATUSES:
            has_active = True

    return has_active


# ==========================================================
# Rendering Helpers
# ==========================================================


def request_title(
    record: dict[str, Any],
) -> str:
    """
    Create a compact sidebar title.
    """

    query = " ".join(
        record["query"].split()
    )

    if len(query) <= 34:
        return query

    return query[:34] + "..."


def status_icon(
    status: str,
) -> str:
    """
    Return compact Request status icon.
    """

    icons = {
        "submitted": "○",
        "running": "●",
        "completed": "✓",
        "failed": "✕",
        "cancelled": "–",
    }

    return icons.get(
        status,
        "•",
    )


def status_label(
    status: str,
) -> str:
    """
    Return human-readable Request status.
    """

    labels = {
        "submitted": "Waiting",
        "running": "Working",
        "completed": "Completed",
        "failed": "Failed",
        "cancelled": "Cancelled",
    }

    return labels.get(
        status,
        status.title(),
    )


def extract_result(
    result: Any,
) -> str:
    """
    Extract final response content.
    """

    if result is None:
        return ""

    if isinstance(
        result,
        dict,
    ):
        content = result.get(
            "content"
        )

        if content is not None:
            return str(
                content
            )

    return str(
        result
    )


def task_status_icon(
    status: str,
) -> str:
    """
    Return compact projected Task status.
    """

    icons = {
        "delegated": "○",
        "waiting": "○",
        "running": "●",
        "completed": "✓",
        "failed": "✕",
    }

    return icons.get(
        status,
        "•",
    )


def render_activity(
    activity: dict[str, Any],
) -> None:
    """
    Render user-facing live KernelAI activity.
    """

    status = activity[
        "status"
    ]

    if status in {
        "completed",
        "failed",
        "cancelled",
    }:
        return

    current_activity = activity.get(
        "activity",
        "Working",
    )

    st.caption(
        f"● {current_activity}"
    )

    task_count = activity.get(
        "task_count",
        0,
    )

    if task_count == 0:
        return

    active_tasks = activity.get(
        "active_tasks",
        0,
    )

    waiting_tasks = activity.get(
        "waiting_tasks",
        0,
    )

    completed_tasks = activity.get(
        "completed_tasks",
        0,
    )

    failed_tasks = activity.get(
        "failed_tasks",
        0,
    )

    st.caption(
        (
            f"{completed_tasks} completed · "
            f"{active_tasks} running · "
            f"{waiting_tasks} waiting"
            + (
                f" · {failed_tasks} failed"
                if failed_tasks
                else ""
            )
        )
    )

    tasks = activity.get(
        "tasks",
        [],
    )

    if not tasks:
        return

    with st.expander(
        "Tasks",
        expanded=True,
    ):
        for task in tasks:
            description = (
                task.get("description")
                or task["task_id"]
            )

            task_status = task.get(
                "status",
                "unknown",
            )

            icon = task_status_icon(
                task_status
            )

            st.markdown(
                f"{icon} {description}"
            )

            skills = task.get(
                "skills",
                [],
            )

            current_tool = task.get(
                "current_tool"
            )

            details: list[str] = []

            if skills:
                details.append(
                    ", ".join(skills)
                )

            if current_tool:
                details.append(
                    f"using {current_tool}"
                )

            if details:
                st.caption(
                    " · ".join(details)
                )


def render_assistant_response(
    record: dict[str, Any],
    activity: dict[str, Any] | None,
) -> None:
    """
    Render KernelAI response or live execution state.
    """

    status = record[
        "status"
    ]

    if status in ACTIVE_STATUSES:

        if activity is not None:
            render_activity(
                activity
            )

        else:
            st.caption(
                "Working on your request..."
            )

        return

    if status == "completed":

        answer = extract_result(
            record.get(
                "result"
            )
        )

        st.markdown(
            answer
        )

        return

    if status == "failed":

        st.error(
            record.get("error")
            or "Request failed."
        )

        return

    if status == "cancelled":

        st.warning(
            "Request cancelled."
        )


# ==========================================================
# Header
# ==========================================================

st.title(
    "KernelAI"
)

st.caption(
    "Ask questions across connected data and the web."
)


# ==========================================================
# Refresh Requests
# ==========================================================

has_active_requests = (
    refresh_active_requests()
)


# ==========================================================
# Sidebar
# ==========================================================

with st.sidebar:

    st.header(
        "KernelAI"
    )

    # ------------------------------------------------------
    # Navigation
    # ------------------------------------------------------

    st.caption(
        "Navigation"
    )

    navigation_columns = st.columns(
        2
    )

    with navigation_columns[0]:

        st.button(
            "💬 Chat",
            use_container_width=True,
            disabled=True,
        )

    with navigation_columns[1]:

        if st.button(
            "📊 Traces",
            use_container_width=True,
        ):
            st.switch_page(
                "pages/1_Observability.py"
            )

    st.divider()

    # ------------------------------------------------------
    # New Request
    # ------------------------------------------------------

    if st.button(
        "＋ New request",
        use_container_width=True,
        type="primary",
    ):
        st.session_state.selected_request_id = None

        st.rerun()

    st.caption(
        "Recent requests"
    )

    # ------------------------------------------------------
    # Request list
    # ------------------------------------------------------

    if not st.session_state.request_order:

        st.caption(
            "No requests yet."
        )

    else:

        for request_id in (
            st.session_state.request_order
        ):

            record = (
                st.session_state.requests[
                    request_id
                ]
            )

            icon = status_icon(
                record["status"]
            )

            title = request_title(
                record
            )

            selected = (
                request_id
                == st.session_state.selected_request_id
            )

            if st.button(
                f"{icon}  {title}",
                key=(
                    f"request-{request_id}"
                ),
                use_container_width=True,
                type=(
                    "primary"
                    if selected
                    else "secondary"
                ),
            ):

                st.session_state.selected_request_id = (
                    request_id
                )

                st.rerun()

            st.caption(
                status_label(
                    record["status"]
                )
            )


# ==========================================================
# Main Conversation
# ==========================================================

selected_request_id = (
    st.session_state.selected_request_id
)


if selected_request_id is None:

    st.markdown(
        """
        ### What can I help you with?
        """
    )

else:

    record = st.session_state.requests.get(
        selected_request_id
    )

    if record is None:

        st.session_state.selected_request_id = None

        st.rerun()

    else:

        activity = None

        if record[
            "status"
        ] in ACTIVE_STATUSES:

            activity = fetch_activity(
                selected_request_id
            )

        # --------------------------------------------------
        # User
        # --------------------------------------------------

        with st.chat_message(
            "user"
        ):

            st.markdown(
                record["query"]
            )

        # --------------------------------------------------
        # KernelAI
        # --------------------------------------------------

        with st.chat_message(
            "assistant"
        ):

            render_assistant_response(
                record,
                activity,
            )


# ==========================================================
# Chat Input
# ==========================================================

prompt = st.chat_input(
    "Ask a question..."
)


# ==========================================================
# Handle New Request
# ==========================================================

if prompt:

    normalized_prompt = (
        prompt.strip()
    )

    if normalized_prompt:

        try:

            create_request(
                normalized_prompt
            )

        except httpx.HTTPStatusError as exc:

            status_code = (
                exc.response.status_code
            )

            try:

                error_body = (
                    exc.response.json()
                )

                detail = error_body.get(
                    "detail",
                    "Request failed.",
                )

            except ValueError:

                detail = (
                    "Request failed."
                )

            st.error(
                (
                    "Backend request failed "
                    f"(HTTP {status_code}): "
                    f"{detail}"
                )
            )

        except httpx.RequestError:

            st.error(
                "Could not communicate with "
                "the KernelAI API."
            )

        else:

            st.rerun()


# ==========================================================
# Poll active Requests
# ==========================================================

if has_active_requests:

    time.sleep(
        POLL_INTERVAL
    )

    st.rerun()