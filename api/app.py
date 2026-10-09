"""FastAPI interface for KernelAI."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from orchestrator.orchestrator import Orchestrator
from tasks.request import Request


app = FastAPI(
    title="KernelAI",
    version="0.1.0",
)


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


# Long-lived KernelAI runtime.
#
# All HTTP Requests submitted to this API share the same
# Orchestrator and therefore the same Request concurrency limit.

orchestrator = Orchestrator(
    supervisor_max_iterations=10,
    max_concurrent_requests=5,
    max_concurrent_tasks=5,
    verbose=False,
    local=False,
)


# API-facing Request history.
#
# Completed, failed, and cancelled Requests remain here so the UI
# can continue displaying their state and results.

request_store: dict[
    str,
    RequestRecord,
] = {}


# Request IDs currently owned by the API/runtime.
#
# IDs are reserved before background execution begins and released
# once the Request completely leaves the runtime.
#
# Historical IDs are not retained here.

active_request_ids: set[str] = set()


def generate_request_id() -> str:
    """
    Generate and reserve a Request ID that is not currently active.

    UUID collisions are handled internally and are never exposed
    to the frontend.
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
        # The Request has completely left the API/runtime.
        #
        # Its ID may now be reused without violating the active-ID
        # uniqueness invariant.

        active_request_ids.discard(
            request.request_id
        )


@app.post(
    "/requests",
    response_model=RequestRecord,
)
async def submit_request(
    body: SubmitRequestBody,
) -> RequestRecord:
    """
    Submit a new Request to KernelAI.

    The Request ID is generated and reserved by the API.

    KernelAI execution continues asynchronously after this endpoint
    returns.
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