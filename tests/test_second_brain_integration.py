"""Live black-box E2E test for the Second Brain runtime.

Requires the full KernelAI stack to already be running.

Flow:

pytest
  -> FastAPI :8000
  -> Orchestrator
  -> Supervisor
  -> real LLM
  -> TaskExecutor
  -> real Worker
  -> Second Brain / get_skill
  -> MCP :8001
  -> final response

Run with:

    pytest tests/test_second_brain_integration.py -v -s
"""

from __future__ import annotations

import asyncio

import httpx
import pytest


API_BASE_URL = "http://127.0.0.1:8000"

POLL_INTERVAL_SECONDS = 1
REQUEST_TIMEOUT_SECONDS = 180


@pytest.mark.asyncio
async def test_real_second_brain_request() -> None:

    async with httpx.AsyncClient(
        base_url=API_BASE_URL,
        timeout=30.0,
    ) as client:

        # -----------------------------------------------------
        # Health check
        # -----------------------------------------------------

        response = await client.get("/health")

        assert response.status_code == 200, (
            "KernelAI FastAPI server is not healthy."
        )

        print("\n[TEST] KernelAI API is healthy.")

        # -----------------------------------------------------
        # Submit a REAL Request through FastAPI
        # -----------------------------------------------------

        query = (
            "Inspect this repository and identify one concrete "
            "code-quality issue. Explain where it occurs and why "
            "it matters. Do not modify any files."
        )

        print("\n[TEST] Sending Request:")
        print(query)

        response = await client.post(
            "/requests",
            json={
                "query": query,
            },
        )

        assert response.status_code == 200, (
            f"Request submission failed: {response.text}"
        )

        submitted = response.json()

        request_id = submitted["request_id"]

        print(
            f"\n[TEST] Request submitted: {request_id}"
        )

        # -----------------------------------------------------
        # Poll Request status
        # -----------------------------------------------------

        elapsed = 0

        final_record = None

        while elapsed < REQUEST_TIMEOUT_SECONDS:

            response = await client.get(
                f"/requests/{request_id}"
            )

            assert response.status_code == 200

            record = response.json()

            status = record["status"]

            print(
                f"[TEST] {elapsed:03d}s "
                f"{request_id} -> {status}"
            )

            if status == "completed":
                final_record = record
                break

            if status == "failed":

                print(
                    "\n[TEST] REQUEST FAILED:"
                )

                print(record)

                pytest.fail(
                    f"KernelAI Request failed: {record}"
                )

            await asyncio.sleep(
                POLL_INTERVAL_SECONDS
            )

            elapsed += POLL_INTERVAL_SECONDS

        if final_record is None:
            pytest.fail(
                "KernelAI Request did not complete within "
                f"{REQUEST_TIMEOUT_SECONDS} seconds."
            )

        # -----------------------------------------------------
        # Final result
        # -----------------------------------------------------

        print(
            "\n========== FINAL RESULT =========="
        )

        print(
            final_record.get("result")
        )

        assert final_record.get("result") is not None

        # -----------------------------------------------------
        # Fetch actual runtime events
        # -----------------------------------------------------

        response = await client.get(
            f"/requests/{request_id}/events"
        )

        assert response.status_code == 200

        event_payload = response.json()

        # API may return the event list directly or wrap it.
        if isinstance(event_payload, list):
            events = event_payload
        else:
            events = event_payload.get(
                "events",
                []
            )

        assert events, (
            "Request completed but no runtime events were returned."
        )

        # -----------------------------------------------------
        # Print runtime activity
        # -----------------------------------------------------

        print(
            "\n========== RUNTIME EVENTS =========="
        )

        for event in events:

            component = event.get(
                "component",
                "?"
            )

            event_name = event.get(
                "event",
                "?"
            )

            task_id = event.get(
                "task_id"
            )

            print(
                f"[{component}] "
                f"{event_name}"
                + (
                    f" task={task_id}"
                    if task_id
                    else ""
                )
            )

        # -----------------------------------------------------
        # Architecture assertions
        # -----------------------------------------------------

        event_names = {
            event.get("event")
            for event in events
        }

        # Request actually went through Orchestrator.
        assert "request_submitted" in event_names
        assert "request_admitted" in event_names

        # Real Supervisor ran.
        assert "supervisor_started" in event_names
        assert "supervisor_completed" in event_names

        # Request reached completion.
        assert "request_completed" in event_names

        # -----------------------------------------------------
        # A real Task must have been delegated
        # -----------------------------------------------------

        task_events = [
            event
            for event in events
            if event.get("task_id")
        ]

        assert task_events, (
            "Supervisor completed the Request without "
            "delegating a Task."
        )

        # -----------------------------------------------------
        # A real Task Worker must have run
        # -----------------------------------------------------

        worker_events = [
            event
            for event in events
            if (
                event.get("component")
                == "generic_worker"
                and event.get("task_id")
            )
        ]

        assert worker_events, (
            "No Task Worker execution was observed."
        )

        # -----------------------------------------------------
        # Second Brain must actually be exercised
        # -----------------------------------------------------

        skill_requests = [
            event
            for event in events
            if event.get("event")
            == "skill_requested"
        ]

        skill_loads = [
            event
            for event in events
            if event.get("event")
            == "skill_loaded"
        ]

        assert skill_requests, (
            "Worker ran but never requested a "
            "Second Brain skill."
        )

        assert skill_loads, (
            "Worker requested a skill but no skill "
            "was successfully loaded."
        )

        # -----------------------------------------------------
        # Verify coding methodology was used
        # -----------------------------------------------------

        coding_skill_loads = [
            event
            for event in skill_loads
            if str(
                event.get(
                    "domain",
                    event.get(
                        "data",
                        {},
                    ).get(
                        "domain",
                        "",
                    ),
                )
            ).lower()
            == "coding"
        ]

        assert coding_skill_loads, (
            "Repository inspection completed without "
            "loading a coding-domain skill."
        )

        loaded_skills = []

        for event in skill_loads:

            skill_id = event.get(
                "skill_id"
            )

            if skill_id is None:
                skill_id = event.get(
                    "data",
                    {},
                ).get(
                    "skill_id"
                )

            if skill_id:
                loaded_skills.append(
                    skill_id
                )

        print(
            "\n[TEST] Loaded skills:",
            loaded_skills,
        )

        assert any(
            str(skill).startswith(
                "coding."
            )
            for skill in loaded_skills
        )

        print(
            "\n[TEST] "
            "Second Brain E2E request PASSED."
        )