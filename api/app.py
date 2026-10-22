"""FastAPI interface for KernelAI."""

from __future__ import annotations

import asyncio
import json
import statistics
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from learning.config.loader import load_learning_config
from learning.evaluation.loader import (
    get_trace_directory,
    load_executions,
)
from learning.models import LearningMode, TracePlane
from learning.policies.store import (
    CANDIDATE_ROOT,
    POLICY_PATHS,
    VERSION_ROOT,
)
from observability.event import RuntimeEvent
from observability.projection import project_request
from observability.sinks.frontend import FrontendSink
from observability.sinks.jsonl import JSONLSink
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

# Offline-learning trace persistence.
#
# JSONLSink appends the same flattened RuntimeEvents consumed by
# learning.evaluation.loader. One file accumulates many Requests;
# load_executions() groups them by request_id.

PRODUCTION_TRACE_PATH = (
    Path(__file__).resolve().parent.parent
    / "learning"
    / "data"
    / "traces"
    / "production"
    / "runtime.jsonl"
)

jsonl_sink = JSONLSink(
    PRODUCTION_TRACE_PATH
)


# Shared runtime tracer.

tracer = RuntimeTracer(
    sinks=[
        TerminalSink(),
        frontend_sink,
        jsonl_sink,
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


# ============================================================
# Offline learning dashboard
# ============================================================

# Read-only projection over persisted offline-learning state.
#
# This endpoint never invokes the learning pipeline, the
# semantic judge, candidate creation, or release logic. It only
# reads configuration and artifacts that already exist on disk.

LEARNING_DATA_ROOT = (
    Path(__file__).resolve().parent.parent
    / "learning"
    / "data"
)

# Mirrors the registry layout written by the manual release
# boundary (learning.release.REGISTRY_ROOT) without importing
# release logic into the API.

RELEASE_REGISTRY_ROOT = (
    LEARNING_DATA_ROOT
    / "registry"
)

EVALUATION_ROOT = (
    LEARNING_DATA_ROOT
    / "evaluations"
)


def count_production_executions() -> int:
    """
    Count reconstructed production executions across all trace
    files.

    Each JSONL file may contain many Requests, so executions are
    counted with the existing loader rather than by counting
    lines. A missing production directory means zero executions.
    """

    directory = get_trace_directory(
        TracePlane.PRODUCTION
    )

    if not directory.is_dir():
        return 0

    total = 0

    for path in sorted(
        directory.glob("*.jsonl")
    ):
        try:
            total += len(
                load_executions(path)
            )

        except (OSError, ValueError):
            # A partially written trace file must not take
            # down the read-only dashboard.

            continue

    return total


def count_files(
    directory: Path,
    pattern: str,
) -> int:
    """
    Count files matching one pattern inside one directory.

    A missing directory counts as zero files.
    """

    if not directory.is_dir():
        return 0

    return sum(
        1
        for path in directory.glob(pattern)
        if path.is_file()
    )


def latest_file_stem(
    directory: Path,
    pattern: str,
) -> str | None:
    """
    Return the stem of the lexicographically latest matching
    file, or None when no file exists.

    Artifact IDs are time-sortable, so filename order is
    chronological order.
    """

    if not directory.is_dir():
        return None

    files = sorted(
        path
        for path in directory.glob(pattern)
        if path.is_file()
    )

    if not files:
        return None

    return files[-1].stem


def read_release_records(
    policy_name: str,
) -> list[dict[str, Any]]:
    """
    Read valid release metadata records for one policy, oldest
    to newest.

    Malformed registry files are ignored rather than failing
    the whole dashboard.
    """

    directory = (
        RELEASE_REGISTRY_ROOT
        / policy_name
    )

    if not directory.is_dir():
        return []

    records: list[dict[str, Any]] = []

    for path in sorted(
        directory.glob("*.json")
    ):
        try:
            with path.open(
                "r",
                encoding="utf-8",
            ) as file:
                metadata = json.load(file)

        except (OSError, ValueError):
            continue

        if isinstance(
            metadata,
            dict,
        ):
            records.append(metadata)

    return records


def aggregate_execution_metrics(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Deterministically aggregate per-execution metric values from
    one persisted evaluation payload.

    This is pure read-only arithmetic over already-scored
    metrics. Only numeric values are included; booleans count
    as 1.0 / 0.0 and missing metrics stay absent.
    """

    executions = payload.get(
        "executions"
    )

    if not isinstance(
        executions,
        list,
    ):
        return []

    values_by_name: dict[
        str,
        list[float],
    ] = {}

    for execution in executions:

        if not isinstance(
            execution,
            dict,
        ):
            continue

        metrics = execution.get(
            "metrics"
        )

        if not isinstance(
            metrics,
            list,
        ):
            continue

        for metric in metrics:

            if not isinstance(
                metric,
                dict,
            ):
                continue

            name = metric.get("name")

            value = metric.get("value")

            if not isinstance(name, str) or not name:
                continue

            if isinstance(value, bool):
                value = 1.0 if value else 0.0

            elif not isinstance(
                value,
                (int, float),
            ):
                continue

            values_by_name.setdefault(
                name,
                [],
            ).append(float(value))

    return [
        {
            "name": name,
            "sample_count": len(values),
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "minimum": min(values),
            "maximum": max(values),
        }
        for name, values in values_by_name.items()
    ]


def load_evaluation_history() -> list[
    dict[str, Any]
]:
    """
    Load all valid persisted evaluations, oldest to newest by
    their time-sortable filenames.

    Aggregate metrics are used when persisted; otherwise they
    are derived from the persisted per-execution scores.
    Policy and version associations are only reported when
    explicitly present in the persisted data.
    """

    if not EVALUATION_ROOT.is_dir():
        return []

    history: list[dict[str, Any]] = []

    for path in sorted(
        EVALUATION_ROOT.glob("eval_*.json")
    ):
        try:
            with path.open(
                "r",
                encoding="utf-8",
            ) as file:
                payload = json.load(file)

        except (OSError, ValueError):
            continue

        if not isinstance(
            payload,
            dict,
        ):
            continue

        aggregate_metrics = payload.get(
            "aggregate_metrics"
        )

        if not isinstance(
            aggregate_metrics,
            list,
        ):
            aggregate_metrics = (
                aggregate_execution_metrics(
                    payload
                )
            )

        execution_count = payload.get(
            "execution_count"
        )

        if (
            not isinstance(execution_count, int)
            or isinstance(execution_count, bool)
        ):
            executions = payload.get(
                "executions"
            )

            execution_count = (
                len(executions)
                if isinstance(executions, list)
                else None
            )

        evaluation_id = payload.get(
            "evaluation_id"
        )

        policy_name = payload.get("policy_name")

        version_id = payload.get("version_id")

        history.append(
            {
                "evaluation_id": (
                    evaluation_id
                    if isinstance(
                        evaluation_id,
                        str,
                    )
                    else path.stem
                ),
                "plane": (
                    payload.get("plane")
                    if isinstance(
                        payload.get("plane"),
                        str,
                    )
                    else None
                ),
                "execution_count": execution_count,
                "policy_name": (
                    policy_name
                    if isinstance(policy_name, str)
                    else None
                ),
                "version_id": (
                    version_id
                    if isinstance(version_id, str)
                    else None
                ),
                "aggregate_metrics": aggregate_metrics,
            }
        )

    return history


def build_learning_dashboard() -> dict[str, Any]:
    """
    Build the read-only offline-learning dashboard payload from
    persisted state only.
    """

    config = load_learning_config()

    evaluation_history = (
        load_evaluation_history()
    )

    policies: list[dict[str, Any]] = []

    total_candidates = 0
    total_releases = 0

    for policy_name in POLICY_PATHS:

        policy_config = config.policies.get(
            policy_name
        )

        if policy_config is not None:
            learning_mode = (
                policy_config.learning.value
            )

        else:
            learning_mode = (
                LearningMode.DISABLED.value
            )

        version_count = count_files(
            VERSION_ROOT / policy_name,
            "*.md",
        )

        policy_candidate_count = count_files(
            CANDIDATE_ROOT / policy_name,
            "*.md",
        )

        release_records = read_release_records(
            policy_name
        )

        total_candidates += (
            policy_candidate_count
        )

        total_releases += len(release_records)

        # The current production version is only known when a
        # valid release record explicitly proves which candidate
        # was promoted. It is never inferred from candidates or
        # version snapshots.

        latest_release_id = None
        current_version_id = None

        if release_records:
            latest_release = release_records[-1]

            release_id = latest_release.get(
                "release_id"
            )

            if isinstance(release_id, str):
                latest_release_id = release_id

            candidate_id = latest_release.get(
                "candidate_id"
            )

            if isinstance(candidate_id, str):
                current_version_id = candidate_id

        policies.append(
            {
                "policy_name": policy_name,
                "learning_mode": learning_mode,
                "current": {
                    "source": "production",
                    "version_id": current_version_id,
                },
                "version_count": version_count,
                "candidate_count": policy_candidate_count,
                "latest_version_id": latest_file_stem(
                    VERSION_ROOT / policy_name,
                    "*.md",
                ),
                "latest_candidate_id": latest_file_stem(
                    CANDIDATE_ROOT / policy_name,
                    "*.md",
                ),
                "latest_release_id": latest_release_id,
            }
        )

    return {
        "enabled": config.enabled,
        "production_execution_count": (
            count_production_executions()
        ),
        "summary": {
            "evaluation_count": len(
                evaluation_history
            ),
            "policy_count": len(policies),
            "candidate_count": total_candidates,
            "release_count": total_releases,
        },
        "policies": policies,
        "latest_evaluation": (
            evaluation_history[-1]
            if evaluation_history
            else None
        ),
        "evaluation_history": evaluation_history,
    }


@app.get(
    "/dashboard/learning",
)
async def get_dashboard_learning() -> dict[str, Any]:
    """
    Return the read-only offline-learning dashboard state.

    Covers production trace volume, learning configuration,
    policy/version/candidate/release state, and evaluation
    history. Performs no learning actions and no writes.
    """

    return build_learning_dashboard()