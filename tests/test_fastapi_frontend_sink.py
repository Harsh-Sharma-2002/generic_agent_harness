"""Integration tests for FastAPI + FrontendSink observability."""

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


@pytest.fixture(autouse=True)
def clear_api_state() -> None:
    """
    Start each test with clean API-facing state.

    FrontendSink is intentionally not cleared here because its
    request IDs are UUID-based and therefore isolated between runs.
    """

    active_request_ids.clear()
    request_store.clear()


@pytest.mark.asyncio
async def test_request_events_available_while_running() -> None:
    """
    Submit one real KernelAI Request and verify that FrontendSink
    events become available through FastAPI before execution
    completes.
    """

    transport = ASGITransport(
        app=app,
    )

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:

        # -----------------------------------------------------
        # Submit Request
        # -----------------------------------------------------

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

        # -----------------------------------------------------
        # Wait until runtime events appear
        # -----------------------------------------------------

        observed_live_events = None

        for _ in range(30):

            response = await client.get(
                f"/requests/{request_id}/events"
            )

            assert response.status_code == 200

            events = response.json()

            if events:
                observed_live_events = events
                break

            await asyncio.sleep(
                0.1
            )

        assert observed_live_events is not None
        assert observed_live_events

        # -----------------------------------------------------
        # Validate event structure
        # -----------------------------------------------------

        first_event = observed_live_events[
            0
        ]

        assert "timestamp" in first_event
        assert "component" in first_event
        assert "event" in first_event
        assert "request_id" in first_event
        assert "task_id" in first_event

        assert all(
            event["request_id"] == request_id
            for event in observed_live_events
        )

        # -----------------------------------------------------
        # Request lifecycle should have started
        # -----------------------------------------------------

        event_names = {
            event["event"]
            for event in observed_live_events
        }

        assert "request_submitted" in event_names

        # -----------------------------------------------------
        # Verify FrontendSink directly
        # -----------------------------------------------------

        sink_events = (
            frontend_sink.get_request_events(
                request_id
            )
        )

        assert sink_events

        assert all(
            event.request_id == request_id
            for event in sink_events
        )

        # -----------------------------------------------------
        # Wait for Request completion
        # -----------------------------------------------------

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

        # -----------------------------------------------------
        # Fetch complete trace
        # -----------------------------------------------------

        response = await client.get(
            f"/requests/{request_id}/events"
        )

        assert response.status_code == 200

        final_events = response.json()

        assert final_events

        # The trace should have grown during execution.

        assert (
            len(final_events)
            >= len(observed_live_events)
        )

        # -----------------------------------------------------
        # Validate Request lifecycle
        # -----------------------------------------------------

        final_event_names = [
            event["event"]
            for event in final_events
        ]

        assert (
            "request_submitted"
            in final_event_names
        )

        assert (
            "request_admitted"
            in final_event_names
        )

        assert (
            "request_completed"
            in final_event_names
        )

        assert (
            "request_failed"
            not in final_event_names
        )

        # -----------------------------------------------------
        # Validate Supervisor lifecycle
        # -----------------------------------------------------

        supervisor_events = [
            event
            for event in final_events
            if event["component"]
            == "supervisor"
        ]

        supervisor_event_names = {
            event["event"]
            for event in supervisor_events
        }

        assert (
            "supervisor_started"
            in supervisor_event_names
        )

        assert (
            "supervisor_completed"
            in supervisor_event_names
        )

        # -----------------------------------------------------
        # Validate Task delegation
        # -----------------------------------------------------

        delegated = [
            event
            for event in supervisor_events
            if event["event"]
            == "task_delegated"
        ]

        assert delegated

        delegated_task_ids = {
            event["task_id"]
            for event in delegated
        }

        assert all(
            task_id is not None
            for task_id in delegated_task_ids
        )

        # Task descriptions from the Supervisor LLM should
        # be preserved by the FrontendSink/API trace.

        assert all(
            event.get("task")
            for event in delegated
        )

        assert all(
            event.get("skills")
            for event in delegated
        )

        # -----------------------------------------------------
        # Validate Task lifecycle
        # -----------------------------------------------------

        task_started = [
            event
            for event in final_events
            if (
                event["component"]
                == "supervisor"
                and event["event"]
                == "task_started"
            )
        ]

        task_completed = [
            event
            for event in final_events
            if (
                event["component"]
                == "task_executor"
                and event["event"]
                == "task_completed"
            )
        ]

        assert task_started
        assert task_completed

        started_task_ids = {
            event["task_id"]
            for event in task_started
        }

        completed_task_ids = {
            event["task_id"]
            for event in task_completed
        }

        assert (
            delegated_task_ids
            == started_task_ids
        )

        assert (
            delegated_task_ids
            == completed_task_ids
        )

        # -----------------------------------------------------
        # Validate GenericWorker trace
        # -----------------------------------------------------

        worker_events = [
            event
            for event in final_events
            if event["component"]
            == "generic_worker"
        ]

        assert worker_events

        supervisor_worker_events = [
            event
            for event in worker_events
            if event.get("worker_role")
            == "supervisor"
        ]

        task_worker_events = [
            event
            for event in worker_events
            if event.get("worker_role")
            == "task"
        ]

        assert supervisor_worker_events
        assert task_worker_events

        # Supervisor worker belongs to the Request itself.

        assert all(
            event["task_id"] is None
            for event in supervisor_worker_events
        )

        # Task workers must remain correlated with Tasks.

        assert all(
            event["task_id"]
            in delegated_task_ids
            for event in task_worker_events
        )

        # -----------------------------------------------------
        # Validate LLM events
        # -----------------------------------------------------

        llm_started = [
            event
            for event in worker_events
            if event["event"]
            == "llm_call_started"
        ]

        llm_completed = [
            event
            for event in worker_events
            if event["event"]
            == "llm_call_completed"
        ]

        assert llm_started
        assert llm_completed

        assert (
            len(llm_started)
            == len(llm_completed)
        )

        # -----------------------------------------------------
        # Validate tool events
        # -----------------------------------------------------

        tool_started = [
            event
            for event in worker_events
            if event["event"]
            == "tool_call_started"
        ]

        assert tool_started

        assert all(
            event.get("tool_name")
            for event in tool_started
        )

        # -----------------------------------------------------
        # Event ordering
        # -----------------------------------------------------

        timestamps = [
            event["timestamp"]
            for event in final_events
        ]

        assert timestamps == sorted(
            timestamps
        )

        # -----------------------------------------------------
        # Request left active runtime but trace remains
        # -----------------------------------------------------

        assert (
            request_id
            not in active_request_ids
        )

        assert request_id in request_store

        assert (
            frontend_sink.get_request_events(
                request_id
            )
        )


@pytest.mark.asyncio
async def test_unknown_request_events_returns_404() -> None:
    """
    Asking for trace events for an unknown Request should return 404.
    """

    transport = ASGITransport(
        app=app,
    )

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:

        response = await client.get(
            "/requests/req_does_not_exist/events"
        )

        assert response.status_code == 404

        body = response.json()

        assert (
            body["detail"]
            == "Request not found."
        )

