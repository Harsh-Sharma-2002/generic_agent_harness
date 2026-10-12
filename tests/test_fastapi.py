"""Integration tests for the KernelAI FastAPI interface."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from api.app import (
    active_request_ids,
    app,
    request_store,
)


@pytest.fixture(autouse=True)
def clear_api_state() -> None:
    """
    Start every API test with clean in-memory API state.
    """

    active_request_ids.clear()
    request_store.clear()


@pytest.mark.asyncio
async def test_submit_and_complete_request() -> None:
    """
    Submit one real KernelAI Request through FastAPI and verify its
    complete API lifecycle.
    """

    transport = ASGITransport(
        app=app,
    )

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:

        # Submit Request.

        response = await client.post(
            "/requests",
            json={
                "query": (
                    "Determine how many customers are currently "
                    "stored in the database."
                ),
            },
        )

        assert response.status_code == 200

        submitted = response.json()

        request_id = submitted[
            "request_id"
        ]

        assert request_id.startswith(
            "req_"
        )

        assert submitted["status"] == "submitted"
        assert submitted["result"] is None
        assert submitted["error"] is None

        # FastAPI should have reserved the ID before returning.

        assert (
            request_id
            in active_request_ids
        )

        # API record should exist immediately.

        assert request_id in request_store

        # Poll until KernelAI finishes.

        final_record = None

        for _ in range(180):
            response = await client.get(
                f"/requests/{request_id}"
            )

            assert response.status_code == 200

            record = response.json()

            if record["status"] in {
                "completed",
                "failed",
                "cancelled",
            }:
                final_record = record
                break

            await asyncio.sleep(
                1
            )

        assert final_record is not None

        assert (
            final_record["status"]
            == "completed"
        )

        assert final_record["result"]
        assert final_record["result"].get(
            "content"
        )

        assert final_record["error"] is None

        # Request has left the active runtime.

        assert (
            request_id
            not in active_request_ids
        )

        # But its API record remains available for UI/history.

        assert request_id in request_store

        response = await client.get(
            f"/requests/{request_id}"
        )

        assert response.status_code == 200

        persisted = response.json()

        assert persisted["status"] == "completed"
        assert persisted["result"]


@pytest.mark.asyncio
async def test_unknown_request_returns_404() -> None:
    """
    Requesting an ID unknown to the API should return HTTP 404.
    """

    transport = ASGITransport(
        app=app,
    )

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:

        response = await client.get(
            "/requests/req_does_not_exist"
        )

        assert response.status_code == 404

        body = response.json()

        assert body["detail"] == (
            "Request not found."
        )


@pytest.mark.asyncio
async def test_multiple_requests_can_be_active() -> None:
    """
    Multiple HTTP submissions should be accepted independently
    while KernelAI executes them concurrently.
    """

    transport = ASGITransport(
        app=app,
    )

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:

        queries = [
            (
                "Determine how many customers are currently "
                "stored in the database."
            ),
            (
                "Determine how many products are currently "
                "stored in the database."
            ),
            (
                "Find the most expensive product in the database "
                "and report its name and price."
            ),
        ]

        responses = await asyncio.gather(
            *[
                client.post(
                    "/requests",
                    json={
                        "query": query,
                    },
                )
                for query in queries
            ]
        )

        assert all(
            response.status_code == 200
            for response in responses
        )

        submitted = [
            response.json()
            for response in responses
        ]

        request_ids = [
            record["request_id"]
            for record in submitted
        ]

        # Every HTTP submission must receive a unique ID.

        assert (
            len(set(request_ids))
            == len(request_ids)
        )

        assert all(
            request_id.startswith("req_")
            for request_id in request_ids
        )

        # All IDs should have been reserved before the HTTP calls
        # returned.

        assert all(
            request_id
            in active_request_ids
            for request_id in request_ids
        )

        # Wait for every Request to finish.

        final_records: dict[
            str,
            dict,
        ] = {}

        for _ in range(180):

            for request_id in request_ids:

                if request_id in final_records:
                    continue

                response = await client.get(
                    f"/requests/{request_id}"
                )

                assert response.status_code == 200

                record = response.json()

                if record["status"] in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    final_records[
                        request_id
                    ] = record

            if (
                len(final_records)
                == len(request_ids)
            ):
                break

            await asyncio.sleep(
                1
            )

        assert (
            len(final_records)
            == len(request_ids)
        )

        assert all(
            record["status"] == "completed"
            for record in final_records.values()
        )

        assert all(
            record["result"]
            and record["result"].get(
                "content"
            )
            for record in final_records.values()
        )

        # Every active-ID reservation must have been released.

        assert all(
            request_id
            not in active_request_ids
            for request_id in request_ids
        )

        # Historical API records remain available.

        assert all(
            request_id in request_store
            for request_id in request_ids
        )
