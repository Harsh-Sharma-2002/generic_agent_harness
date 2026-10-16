"""FastAPI interface for KernelAI."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from observability.event import RuntimeEvent
from observability.projection import project_request
from observability.sinks.frontend import FrontendSink
from observability.sinks.terminal import TerminalSink
from observability.tracer import RuntimeTracer
from orchestrator.orchestrator import Orchestrator
from tasks.request import Request


# ============================================================
# FastAPI
# ============================================================

app = FastAPI(
    title="KernelAI",
    version="0.1.0",
)


# ============================================================
# API models
# ============================================================


class SubmitRequestBody(BaseModel):
    """
    HTTP payload for submitting a KernelAI Request.
    """

    query: str = Field(
        min_length=1,
    )


class RequestRecord(BaseModel):
    """
    API-facing state for one KernelAI Request.
    """

    request_id: str
    query: str
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None


# ============================================================
# Observability
# ============================================================

# Live structured trace store.
#
# FrontendSink preserves raw RuntimeEvents and provides indexed
# access by Request and Task.

frontend_sink = FrontendSink(
    max_events=5000,
)


# Shared runtime tracer.

tracer = RuntimeTracer(
    sinks=[
        TerminalSink(),
        frontend_sink,
    ]
)


# ============================================================
# KernelAI runtime
# ============================================================

# One long-lived Orchestrator is shared across every HTTP client.

orchestrator = Orchestrator(
    supervisor_max_iterations=10,
    max_concurrent_requests=5,
    max_concurrent_tasks=5,
    verbose=False,
    local=False,
    trace=tracer,
)


# ============================================================
# API Request state
# ============================================================

# Historical API-facing Request records.

request_store: dict[
    str,
    RequestRecord,
] = {}


# IDs currently owned by submitted/waiting/running Requests.

active_request_ids: set[str] = set()


# ============================================================
# Request ID generation
# ============================================================


def generate_request_id() -> str:
    """
    Generate and reserve an ID that is not currently active.

    UUID collisions are handled internally.
    """

    while True:
        request_id = (
            f"req_{uuid.uuid4().hex}"
        )

        if request_id in active_request_ids:
            continue

        active_request_ids.add(
            request_id
        )

        return request_id


# ============================================================
# Event serialization
# ============================================================


def serialize_event(
    event: RuntimeEvent,
) -> dict[str, Any]:
    """
    Convert one RuntimeEvent into its public JSON representation.

    Dynamic event metadata is flattened into the top-level object.
    """

    return {
        "timestamp": event.timestamp.isoformat(),
        "component": event.component,
        "event": event.event,
        "request_id": event.request_id,
        "task_id": event.task_id,
        **event.data,
    }


# ============================================================
# Request lookup
# ============================================================


def require_request(
    request_id: str,
) -> RequestRecord:
    """
    Return one API Request record or raise HTTP 404.
    """

    record = request_store.get(
        request_id
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Request not found.",
        )

    return record


# ============================================================
# Empty projection
# ============================================================


def empty_projection(
    record: RequestRecord,
) -> dict[str, Any]:
    """
    Return frontend-safe observability state before the runtime has
    emitted its first event.

    A small interval may exist between FastAPI accepting a Request
    and the Orchestrator emitting REQUEST_SUBMITTED.
    """

    return {
        "request_id": record.request_id,
        "status": record.status,
        "activity": "Waiting for execution",
        "timing": {
            "submitted_at": None,
            "admitted_at": None,
            "completed_at": None,
            "queue_seconds": None,
            "execution_seconds": None,
            "total_seconds": None,
        },
        "supervisor": {
            "status": "waiting",
            "started_at": None,
            "completed_at": None,
            "duration_seconds": None,
        },
        "task_count": 0,
        "active_tasks": 0,
        "waiting_tasks": 0,
        "completed_tasks": 0,
        "failed_tasks": 0,
        "tasks": [],
        "metrics": {
            "llm_calls": 0,
            "tool_calls": 0,
            "tool_failures": 0,
            "llm_failures": 0,
        },
        "errors": [],
    }


# ============================================================
# Projection lookup
# ============================================================


def get_request_projection(
    request_id: str,
) -> dict[str, Any]:
    """
    Return the current observability projection for one Request.
    """

    record = require_request(
        request_id
    )

    events = frontend_sink.get_request_events(
        request_id
    )

    if not events:
        return empty_projection(
            record
        )

    return project_request(
        events
    )


# ============================================================
# Background Request execution
# ============================================================


async def execute_request(
    request: Request,
) -> None:
    """
    Execute one KernelAI Request in the background and update its
    API-facing RequestRecord.
    """

    record = request_store[
        request.request_id
    ]

    record.status = "running"

    try:
        result = await orchestrator.submit(
            request
        )

        record.status = "completed"
        record.result = result

    except asyncio.CancelledError:
        record.status = "cancelled"
        raise

    except Exception as exc:
        record.status = "failed"
        record.error = str(exc)

    finally:
        # The Request has completely left the active runtime.
        #
        # Historical RequestRecord and FrontendSink trace data remain.

        active_request_ids.discard(
            request.request_id
        )


@app.get("/health")
async def health() -> dict[str, str]:
    """
    Return API health status.
    """
    return {
        "status": "ok",
    }
# ============================================================
# Submit Request
# ============================================================


@app.post(
    "/requests",
    response_model=RequestRecord,
)
async def submit_request(
    body: SubmitRequestBody,
) -> RequestRecord:
    """
    Submit one Request to KernelAI.

    Execution continues asynchronously after this endpoint returns.
    """

    request_id = generate_request_id()

    request = Request(
        request_id=request_id,
        query=body.query.strip(),
    )

    record = RequestRecord(
        request_id=request_id,
        query=request.query,
        status="submitted",
    )

    request_store[
        request_id
    ] = record

    asyncio.create_task(
        execute_request(
            request
        )
    )

    return record


# ============================================================
# Request state
# ============================================================


@app.get(
    "/requests/{request_id}",
    response_model=RequestRecord,
)
async def get_request(
    request_id: str,
) -> RequestRecord:
    """
    Return the API-facing state of one Request.
    """

    return require_request(
        request_id
    )


# ============================================================
# User-facing activity projection
# ============================================================


@app.get(
    "/requests/{request_id}/activity",
)
async def get_request_activity(
    request_id: str,
) -> dict[str, Any]:
    """
    Return projected live activity for one Request.

    Intended for normal frontend consumers.
    """

    return get_request_projection(
        request_id
    )


# ============================================================
# Raw Request events
# ============================================================


@app.get(
    "/requests/{request_id}/events",
)
async def get_request_events(
    request_id: str,
) -> list[dict[str, Any]]:
    """
    Return raw retained RuntimeEvents for one Request.
    """

    require_request(
        request_id
    )

    events = frontend_sink.get_request_events(
        request_id
    )

    return [
        serialize_event(event)
        for event in events
    ]


# ============================================================
# Dashboard Request list
# ============================================================


@app.get(
    "/dashboard/requests",
)
async def get_dashboard_requests() -> list[dict[str, Any]]:
    """
    Return all Requests currently known to the API.

    The dashboard uses this endpoint for Request discovery and
    navigation. Human-facing query text is included so dashboard
    users do not need to know Request IDs.
    """

    dashboard_requests: list[
        dict[str, Any]
    ] = []

    for request_id, record in reversed(
        list(
            request_store.items()
        )
    ):
        events = frontend_sink.get_request_events(
            request_id
        )

        if events:
            projection = project_request(
                events
            )
        else:
            projection = empty_projection(
                record
            )

        dashboard_requests.append(
            {
                "request_id": request_id,
                "query": record.query,
                "status": projection[
                    "status"
                ],
                "activity": projection[
                    "activity"
                ],
                "task_count": projection[
                    "task_count"
                ],
                "active_tasks": projection[
                    "active_tasks"
                ],
                "waiting_tasks": projection[
                    "waiting_tasks"
                ],
                "completed_tasks": projection[
                    "completed_tasks"
                ],
                "failed_tasks": projection[
                    "failed_tasks"
                ],
                "timing": projection[
                    "timing"
                ],
            }
        )

    return dashboard_requests


# ============================================================
# Dashboard Request projection
# ============================================================


@app.get(
    "/dashboard/requests/{request_id}",
)
async def get_dashboard_request(
    request_id: str,
) -> dict[str, Any]:
    """
    Return the structured observability projection for one Request.
    """

    return get_request_projection(
        request_id
    )


# ============================================================
# Dashboard Request events
# ============================================================


@app.get(
    "/dashboard/requests/{request_id}/events",
)
async def get_dashboard_request_events(
    request_id: str,
) -> list[dict[str, Any]]:
    """
    Return the complete retained raw trace for one Request.
    """

    require_request(
        request_id
    )

    events = frontend_sink.get_request_events(
        request_id
    )

    return [
        serialize_event(event)
        for event in events
    ]


# ============================================================
# Dashboard Task events
# ============================================================


@app.get(
    "/dashboard/tasks/{task_id}/events",
)
async def get_dashboard_task_events(
    task_id: str,
) -> list[dict[str, Any]]:
    """
    Return the complete retained raw trace for one Task.
    """

    events = frontend_sink.get_task_events(
        task_id
    )

    if not events:
        raise HTTPException(
            status_code=404,
            detail="Task not found.",
        )

    return [
        serialize_event(event)
        for event in events
    ]