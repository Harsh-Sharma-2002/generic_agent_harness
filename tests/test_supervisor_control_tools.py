"""Tests for Supervisor domain routing and TaskState ownership.

These exercise the Supervisor's routing contract without executing
Tasks, so they need no LLM credentials or database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from second_brain.runtime import SecondBrainRuntime
from supervisor.supervisor import Supervisor
from tasks.models import TaskState
from tasks.request import Request
from tasks.task import TaskStatus


VAULT_PATH = Path(__file__).resolve().parents[1] / "vault"


@pytest.fixture()
def supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> Supervisor:
    """Return a Supervisor bound to the repository vault."""

    monkeypatch.setenv(
        "KERNELAI_VAULT_PATH",
        str(VAULT_PATH),
    )

    return Supervisor(
        request=Request(
            request_id="supervisor-control-test",
            query="Count the customers in the database.",
        ),
        local=True,
    )


@pytest.mark.asyncio
async def test_supervisor_owns_second_brain_and_receives_registry(
    supervisor: Supervisor,
) -> None:
    """The Supervisor starts with REGISTRY, not with domain or skill bodies."""

    assert isinstance(
        supervisor.second_brain,
        SecondBrainRuntime,
    )

    system_prompt = supervisor.agent.system_prompt

    assert "# Skill Registry" in system_prompt
    assert "# KernelAI Supervisor" in system_prompt

    # Only routing-level information is injected up front.
    assert "## When to Use" not in system_prompt

    # TaskState registry starts empty and is owned per Request.
    assert supervisor.task_states == {}

    # Two model-facing control tools: delegation and escalation.
    assert set(supervisor.control_client.tools) == {
        "delegate_task",
        "resolve_escalation",
    }
    assert supervisor.agent.allowed_tools == {
        "delegate_task",
        "resolve_escalation",
    }


def test_resolve_escalation_schema_does_not_restate_authority(
    supervisor: Supervisor,
) -> None:
    """The model picks a decision, not the requested authority."""

    schema, _ = supervisor.control_client.tools[
        "resolve_escalation"
    ]

    parameters = schema["function"]["parameters"]

    assert parameters["required"] == [
        "task_id",
        "decision",
    ]

    assert set(parameters["properties"]) == {
        "task_id",
        "decision",
        "suggestion",
    }

    assert parameters["properties"]["decision"]["enum"] == [
        "approve",
        "deny",
        "terminate",
    ]

    for field in (
        "requested_domain",
        "requested_tool",
        "requested_skill_limit",
    ):
        assert field not in parameters["properties"]


@pytest.mark.asyncio
async def test_delegate_task_creates_task_and_task_state(
    supervisor: Supervisor,
) -> None:
    """Delegation creates a Task and its TaskState under the same task_id."""

    result = await supervisor.control_client.call_tool(
        "delegate_task",
        {
            "task": "  Count the customers.  ",
            "domains": [
                " data ",
                "coding",
                "data",
            ],
        },
    )

    assert isinstance(result, str)

    assert len(supervisor.tasks) == 1
    assert len(supervisor.task_states) == 1

    task_id = next(iter(supervisor.tasks))
    task = supervisor.tasks[task_id]
    task_state = supervisor.task_states[task_id]

    # Same task_id keys both registries.
    assert task.task_id == task_state.task_id == task_id

    # Domains are stripped, deduplicated, and order-preserving.
    assert task.authorized_domains == ["data", "coding"]
    assert task_state.authorized_domains == {"data", "coding"}

    assert task.query == "Count the customers."
    assert task.request_id == "supervisor-control-test"
    assert task.status == TaskStatus.QUEUED

    assert isinstance(task_state, TaskState)
    assert task_state.instruction == "Count the customers."

    # TaskState defaults come from tasks.models, not from the Supervisor.
    assert task_state.attempt == 1
    assert task_state.skill_limit == 5
    assert task_state.loaded_skills == []
    assert task_state.progress == ""
    assert task_state.findings == []
    assert task_state.artifacts == []
    assert task_state.remaining_work == ""
    assert task_state.escalation is None

    # Pending batch behavior is preserved.
    assert supervisor.pending_tasks == [task]

    # The model is told the identifier only.
    assert task_id in result


@pytest.mark.asyncio
async def test_delegate_task_rejects_unknown_domain(
    supervisor: Supervisor,
) -> None:
    """A domain that does not exist in the vault is rejected."""

    with pytest.raises(ValueError, match="Unknown domain"):
        await supervisor.control_client.call_tool(
            "delegate_task",
            {
                "task": "Do something.",
                "domains": ["not_a_domain"],
            },
        )

    # Nothing is registered on a rejected delegation.
    assert supervisor.tasks == {}
    assert supervisor.task_states == {}
    assert supervisor.pending_tasks == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "domains",
    [
        [],
        "coding",
        [""],
        ["   "],
        [1],
        ["coding", ""],
    ],
)
async def test_delegate_task_rejects_invalid_domains(
    supervisor: Supervisor,
    domains: Any,
) -> None:
    """Malformed domain lists are rejected before any Task exists."""

    with pytest.raises(ValueError):
        await supervisor.control_client.call_tool(
            "delegate_task",
            {
                "task": "Do something.",
                "domains": domains,
            },
        )

    assert supervisor.tasks == {}
    assert supervisor.task_states == {}


@pytest.mark.asyncio
async def test_supervisor_requires_vault_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing vault path is a configuration error, not a fallback."""

    monkeypatch.delenv("KERNELAI_VAULT_PATH", raising=False)

    with pytest.raises(ValueError, match="KERNELAI_VAULT_PATH"):
        Supervisor(
            request=Request(
                request_id="supervisor-config-test",
                query="Hello.",
            ),
            local=True,
        )
