"""Integration tests for KernelAI observability API endpoints."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from api.app import (
    active_request_ids,
    app,
    frontend_sink,
    request_store,
)


# ============================================================
# Test state
# ============================================================


@pytest.fixture(autouse=True)
def clear_api_state() -> None:
    """
    Start each test with clean API-facing state.

    FrontendSink events from previous tests are harmless because
    every Request receives a new UUID.
    """

    active_request_ids.clear()
    request_store.clear()


# ============================================================
# Full observability API integration
# ============================================================


@pytest.mark.asyncio
async def test_observability_api_lifecycle() -> None:
    """
    Submit one real KernelAI Request and verify the complete
    observability API lifecycle.
    """

    transport = ASGITransport(
        app=app,
    )

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:

        # ----------------------------------------------------
        # Submit Request
        # ----------------------------------------------------

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

        assert submitted["status"] == "submitted"
        assert submitted["result"] is None
        assert submitted["error"] is None

        assert request_id in request_store
        assert request_id in active_request_ids

        # ----------------------------------------------------
        # Dashboard Request discovery
        # ----------------------------------------------------

        response = await client.get(
            "/dashboard/requests"
        )

        assert response.status_code == 200

        dashboard_requests = response.json()

        matching_requests = [
            record
            for record in dashboard_requests
            if record["request_id"]
            == request_id
        ]

        assert len(matching_requests) == 1

        dashboard_record = (
            matching_requests[0]
        )

        assert dashboard_record[
            "query"
        ] == (
            "Determine how many customers are currently "
            "stored in the database."
        )

        assert dashboard_record[
            "status"
        ] in {
            "submitted",
            "running",
        }

        # ----------------------------------------------------
        # Wait for live trace
        # ----------------------------------------------------

        live_activity = None

        for _ in range(100):

            response = await client.get(
                f"/requests/{request_id}/activity"
            )

            assert response.status_code == 200

            activity = response.json()

            if (
                activity["status"] == "running"
                and activity["supervisor"]["status"]
                == "running"
            ):
                live_activity = activity
                break

            await asyncio.sleep(
                0.1
            )

        assert live_activity is not None

        # ----------------------------------------------------
        # Validate live activity projection
        # ----------------------------------------------------

        assert (
            live_activity["request_id"]
            == request_id
        )

        assert (
            live_activity["status"]
            == "running"
        )

        assert live_activity[
            "activity"
        ]

        assert (
            live_activity["timing"][
                "submitted_at"
            ]
            is not None
        )

        assert (
            live_activity["timing"][
                "admitted_at"
            ]
            is not None
        )

        assert (
            live_activity["timing"][
                "completed_at"
            ]
            is None
        )

        assert (
            live_activity["timing"][
                "queue_seconds"
            ]
            is not None
        )

        assert (
            live_activity["timing"][
                "total_seconds"
            ]
            is None
        )

        assert (
            live_activity["supervisor"][
                "status"
            ]
            == "running"
        )

        # ----------------------------------------------------
        # Dashboard projection should match activity projection
        # ----------------------------------------------------

        response = await client.get(
            f"/dashboard/requests/{request_id}"
        )

        assert response.status_code == 200

        dashboard_projection = (
            response.json()
        )

        assert (
            dashboard_projection[
                "request_id"
            ]
            == request_id
        )

        assert (
            dashboard_projection[
                "status"
            ]
            == live_activity["status"]
        )

        # ----------------------------------------------------
        # Raw events should already exist
        # ----------------------------------------------------

        response = await client.get(
            f"/dashboard/requests/{request_id}/events"
        )

        assert response.status_code == 200

        live_events = response.json()

        assert live_events

        assert all(
            event["request_id"]
            == request_id
            for event in live_events
        )

        live_event_names = {
            event["event"]
            for event in live_events
        }

        assert (
            "request_submitted"
            in live_event_names
        )

        assert (
            "request_admitted"
            in live_event_names
        )

        assert (
            "supervisor_started"
            in live_event_names
        )

        # ----------------------------------------------------
        # Wait for Request completion
        # ----------------------------------------------------

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

        assert final_record[
            "result"
        ].get(
            "content"
        )

        assert final_record["error"] is None

        # ----------------------------------------------------
        # Final activity projection
        # ----------------------------------------------------

        response = await client.get(
            f"/requests/{request_id}/activity"
        )

        assert response.status_code == 200

        final_activity = response.json()

        assert (
            final_activity["status"]
            == "completed"
        )

        assert (
            final_activity["activity"]
            == "Completed"
        )

        assert (
            final_activity["timing"][
                "submitted_at"
            ]
            is not None
        )

        assert (
            final_activity["timing"][
                "admitted_at"
            ]
            is not None
        )

        assert (
            final_activity["timing"][
                "completed_at"
            ]
            is not None
        )

        assert (
            final_activity["timing"][
                "queue_seconds"
            ]
            is not None
        )

        assert (
            final_activity["timing"][
                "execution_seconds"
            ]
            is not None
        )

        assert (
            final_activity["timing"][
                "total_seconds"
            ]
            is not None
        )

        assert (
            final_activity["timing"][
                "total_seconds"
            ]
            >= final_activity["timing"][
                "execution_seconds"
            ]
        )

        # ----------------------------------------------------
        # Supervisor final state
        # ----------------------------------------------------

        supervisor = final_activity[
            "supervisor"
        ]

        assert (
            supervisor["status"]
            == "completed"
        )

        assert (
            supervisor["started_at"]
            is not None
        )

        assert (
            supervisor["completed_at"]
            is not None
        )

        assert (
            supervisor["duration_seconds"]
            is not None
        )

        assert (
            supervisor["duration_seconds"]
            >= 0
        )

        # ----------------------------------------------------
        # Tasks
        # ----------------------------------------------------

        assert (
            final_activity["task_count"]
            >= 1
        )

        assert final_activity[
            "tasks"
        ]

        assert (
            final_activity["active_tasks"]
            == 0
        )

        assert (
            final_activity["waiting_tasks"]
            == 0
        )

        assert (
            final_activity["failed_tasks"]
            == 0
        )

        assert (
            final_activity["completed_tasks"]
            == final_activity["task_count"]
        )

        # ----------------------------------------------------
        # Validate every projected Task
        # ----------------------------------------------------

        task_ids: set[str] = set()

        for task in final_activity[
            "tasks"
        ]:

            task_id = task[
                "task_id"
            ]

            task_ids.add(
                task_id
            )

            assert task_id

            assert task[
                "description"
            ]

            assert task[
                "skills"
            ]

            assert (
                task["status"]
                == "completed"
            )

            assert (
                task["delegated_at"]
                is not None
            )

            assert (
                task["started_at"]
                is not None
            )

            assert (
                task["completed_at"]
                is not None
            )

            assert (
                task["queue_seconds"]
                is not None
            )

            assert (
                task["execution_seconds"]
                is not None
            )

            assert (
                task["execution_seconds"]
                >= 0
            )

            assert (
                task["worker"][
                    "status"
                ]
                == "completed"
            )

            assert (
                task["worker"][
                    "llm_calls"
                ]
                >= 1
            )

            # --------------------------------------------
            # Task drill-down endpoint
            # --------------------------------------------

            response = await client.get(
                f"/dashboard/tasks/{task_id}/events"
            )

            assert response.status_code == 200

            task_events = response.json()

            assert task_events

            assert all(
                event["task_id"]
                == task_id
                for event in task_events
            )

        # ----------------------------------------------------
        # Runtime metrics
        # ----------------------------------------------------

        metrics = final_activity[
            "metrics"
        ]

        assert (
            metrics["llm_calls"]
            >= 1
        )

        assert (
            metrics["tool_calls"]
            >= 1
        )

        assert (
            metrics["llm_failures"]
            >= 0
        )

        assert (
            metrics["tool_failures"]
            >= 0
        )

        # ----------------------------------------------------
        # Complete raw trace
        # ----------------------------------------------------

        response = await client.get(
            f"/dashboard/requests/{request_id}/events"
        )

        assert response.status_code == 200

        final_events = response.json()

        assert (
            len(final_events)
            >= len(live_events)
        )

        event_names = {
            event["event"]
            for event in final_events
        }

        assert (
            "request_submitted"
            in event_names
        )

        assert (
            "request_admitted"
            in event_names
        )

        assert (
            "request_completed"
            in event_names
        )

        assert (
            "supervisor_started"
            in event_names
        )

        assert (
            "supervisor_completed"
            in event_names
        )

        assert (
            "task_delegated"
            in event_names
        )

        assert (
            "task_started"
            in event_names
        )

        assert (
            "task_completed"
            in event_names
        )

        assert (
            "llm_call_started"
            in event_names
        )

        assert (
            "llm_call_completed"
            in event_names
        )

        assert (
            "tool_call_started"
            in event_names
        )

        # ----------------------------------------------------
        # Dashboard discovery after completion
        # ----------------------------------------------------

        response = await client.get(
            "/dashboard/requests"
        )

        assert response.status_code == 200

        dashboard_requests = (
            response.json()
        )

        final_dashboard_record = next(
            record
            for record in dashboard_requests
            if record["request_id"]
            == request_id
        )

        assert (
            final_dashboard_record[
                "status"
            ]
            == "completed"
        )

        assert (
            final_dashboard_record[
                "activity"
            ]
            == "Completed"
        )

        assert (
            final_dashboard_record[
                "task_count"
            ]
            == final_activity[
                "task_count"
            ]
        )

        assert (
            final_dashboard_record[
                "completed_tasks"
            ]
            == final_activity[
                "completed_tasks"
            ]
        )

        # ----------------------------------------------------
        # Runtime cleanup / historical retention
        # ----------------------------------------------------

        assert (
            request_id
            not in active_request_ids
        )

        assert (
            request_id
            in request_store
        )

        assert (
            frontend_sink.get_request_events(
                request_id
            )
        )


# ============================================================
# Unknown Request
# ============================================================


@pytest.mark.asyncio
async def test_unknown_observability_request_returns_404() -> None:
    """
    Unknown Request observability endpoints should return HTTP 404.
    """

    transport = ASGITransport(
        app=app,
    )

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:

        endpoints = [
            (
                "/requests/"
                "req_does_not_exist/activity"
            ),
            (
                "/requests/"
                "req_does_not_exist/events"
            ),
            (
                "/dashboard/requests/"
                "req_does_not_exist"
            ),
            (
                "/dashboard/requests/"
                "req_does_not_exist/events"
            ),
        ]

        for endpoint in endpoints:

            response = await client.get(
                endpoint
            )

            assert (
                response.status_code
                == 404
            )

            assert (
                response.json()["detail"]
                == "Request not found."
            )


# ============================================================
# Unknown Task
# ============================================================


@pytest.mark.asyncio
async def test_unknown_dashboard_task_returns_404() -> None:
    """
    Unknown Task drill-down should return HTTP 404.
    """

    transport = ASGITransport(
        app=app,
    )

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:

        response = await client.get(
            (
                "/dashboard/tasks/"
                "task_does_not_exist/events"
            )
        )

        assert (
            response.status_code
            == 404
        )

        assert (
            response.json()["detail"]
            == "Task not found."
        )
