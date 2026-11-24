"""Structural contracts for Supervisor-only methodologies.

These prove the scaffolding only: that Supervisor methodologies exist in
their own vault root, are advertised through the Supervisor index, load
lazily through a Supervisor-only control capability, stay Request-scoped,
and grant no authority. No methodology content is exercised or asserted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from second_brain.models import (
    SecondBrainError,
    SecondBrainErrorCode,
)
from second_brain.runtime import SecondBrainRuntime
from supervisor import control_tools as control_tools_module
from supervisor.supervisor import Supervisor
from tasks.models import TaskState
from tasks.request import Request
from tasks.task_executor import WORKER_CONTROL_TOOLS
from worker.execution_budget import ExecutionBudget
from worker.skill_runtime import SkillToolClient, WorkerSkillRuntime


VAULT_PATH = Path(__file__).resolve().parents[1] / "vault"

SUPERVISOR_SKILL_IDS = (
    "supervisor.coding",
    "supervisor.research",
    "supervisor.teaching",
    "supervisor.data",
    "supervisor.context-allocation",
)

PLACEHOLDER = (
    "PLACEHOLDER — methodology content will be designed separately."
)

METHODOLOGY_HEADINGS = (
    "# Coding Decomposition",
    "# Research Decomposition",
    "# Teaching Decomposition",
    "# Data Decomposition",
    "# Context Allocation",
)


def build_supervisor(
    monkeypatch: pytest.MonkeyPatch,
    request_id: str = "supervisor-skill-test",
) -> Supervisor:
    """Return a Supervisor bound to the repository vault."""

    monkeypatch.setenv(
        "KERNELAI_VAULT_PATH",
        str(VAULT_PATH),
    )

    return Supervisor(
        request=Request(
            request_id=request_id,
            query="Plan the work.",
        ),
        local=True,
    )


def build_worker_runtime(
    authorized_domains: set[str],
) -> tuple[WorkerSkillRuntime, TaskState]:
    """Return a Worker skill runtime with no execution authority."""

    state = TaskState(
        task_id="task-1",
        instruction="Do the work.",
        authorized_domains=set(authorized_domains),
    )

    runtime = WorkerSkillRuntime(
        second_brain=SecondBrainRuntime(VAULT_PATH),
        task_state=state,
        attempt=1,
        request_id="request-1",
        task_id="task-1",
        execution_budget=ExecutionBudget(),
    )

    return runtime, state


# A. Vault discovery


def test_supervisor_index_and_methodologies_are_discoverable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)
    runtime = supervisor.second_brain

    # Distinct roots: the corpora are never merged.
    assert runtime.skills_path != runtime.supervisor_skills_path
    assert runtime.supervisor_skills_path == VAULT_PATH / "supervisor_skills"

    index = runtime.get_supervisor_domain()

    assert isinstance(index, str)
    assert "# Supervisor Methodology Index" in index

    for skill_id in SUPERVISOR_SKILL_IDS:
        assert skill_id in index

        loaded = runtime.get_supervisor_skill(skill_id)

        assert not isinstance(loaded, SecondBrainError)
        assert loaded.metadata.skill_id == skill_id
        assert loaded.metadata.domain == "supervisor"
        assert PLACEHOLDER not in loaded.projection.content
        assert "## Scope discipline" in loaded.projection.content


def test_supervisor_vault_contains_only_methodologies() -> None:
    root = VAULT_PATH / "supervisor_skills"

    directories = {
        path.name for path in root.iterdir() if path.is_dir()
    }

    assert directories == {
        "coding",
        "research",
        "teaching",
        "data",
        "context-allocation",
    }

    assert {
        path.name for path in root.glob("*.md")
    } == {"DOMAIN.md"}


CONTEXT_ALLOCATION_PATH = (
    VAULT_PATH
    / "supervisor_skills"
    / "context-allocation"
    / "context-allocation.md"
)


def test_context_allocation_methodology_is_structurally_valid() -> None:
    """The context methodology uses the Supervisor file convention."""

    assert CONTEXT_ALLOCATION_PATH.is_file()

    runtime = SecondBrainRuntime(VAULT_PATH)

    index = runtime.get_supervisor_domain()

    assert isinstance(index, str)

    # Advertised as a context methodology, not as another work domain.
    assert "## Domain Decomposition Methodologies" in index
    assert "## Context Methodology" in index
    assert index.index("## Context Methodology") < index.index(
        "supervisor.context-allocation"
    )

    loaded = runtime.get_supervisor_skill(
        "supervisor.context-allocation"
    )

    assert not isinstance(loaded, SecondBrainError)
    assert loaded.metadata.skill_id == "supervisor.context-allocation"
    assert loaded.metadata.name == "context-allocation"
    assert loaded.metadata.domain == "supervisor"
    assert loaded.metadata.description.strip()
    assert loaded.metadata.related == ()

    assert "# Context Allocation" in loaded.projection.content


# B. Startup projection


def test_startup_projection_is_index_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    system_prompt = supervisor.agent.system_prompt

    assert "# KernelAI Supervisor" in system_prompt
    assert "# Skill Registry" in system_prompt
    assert "# Supervisor Methodology Index" in system_prompt

    for skill_id in SUPERVISOR_SKILL_IDS:
        assert skill_id in system_prompt

    # No methodology body is injected eagerly.
    for heading in METHODOLOGY_HEADINGS:
        assert heading not in system_prompt

    assert PLACEHOLDER not in system_prompt
    assert "## Keep inside one Task" not in system_prompt

    assert supervisor.loaded_supervisor_skills == []


# C. Lazy loading


@pytest.mark.asyncio
async def test_get_supervisor_skill_loads_and_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    raw = await supervisor.control_client.call_tool(
        "get_supervisor_skill",
        {"skill_id": " supervisor.research "},
    )

    result = json.loads(raw)

    assert result["ok"] is True
    assert result["reused"] is False
    assert result["skill"]["skill_id"] == "supervisor.research"
    assert result["skill"]["name"] == "research"
    assert "# Research Decomposition" in result["skill"]["content"]
    assert "## Keep inside one Task" in result["skill"]["content"]
    assert "PLACEHOLDER" not in result["skill"]["content"]

    assert supervisor.loaded_supervisor_skills == [
        "supervisor.research"
    ]


# D. Reuse


@pytest.mark.asyncio
async def test_loading_the_same_methodology_reuses_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    first = json.loads(
        await supervisor.control_client.call_tool(
            "get_supervisor_skill",
            {"skill_id": "supervisor.research"},
        )
    )

    second = json.loads(
        await supervisor.control_client.call_tool(
            "get_supervisor_skill",
            {"skill_id": "supervisor.research"},
        )
    )

    assert first["reused"] is False
    assert second["reused"] is True
    assert second["skill"] == first["skill"]

    # Loaded once, not duplicated in context.
    assert supervisor.loaded_supervisor_skills == [
        "supervisor.research"
    ]


# E. Role isolation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "worker_skill_id",
    [
        "research.web-search",
        "coding.systematic-debugging",
    ],
)
async def test_supervisor_rejects_a_worker_skill_id(
    monkeypatch: pytest.MonkeyPatch,
    worker_skill_id: str,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    with pytest.raises(
        ValueError,
        match="Invalid Supervisor skill ID",
    ):
        await supervisor.control_client.call_tool(
            "get_supervisor_skill",
            {"skill_id": worker_skill_id},
        )

    assert supervisor.loaded_supervisor_skills == []


@pytest.mark.parametrize(
    "supervisor_skill_id",
    [
        "supervisor.research",
        "supervisor.coding",
        "supervisor.context-allocation",
    ],
)
def test_worker_cannot_load_a_supervisor_methodology(
    supervisor_skill_id: str,
) -> None:
    runtime, state = build_worker_runtime({"research", "coding"})

    result = runtime.load_skill(supervisor_skill_id)

    # The 'supervisor' segment is not a Worker domain, so it is
    # rejected before any filesystem resolution.
    assert result["ok"] is False
    assert result["error"] == (
        SecondBrainErrorCode.UNAUTHORIZED_SKILL.value
    )
    assert state.loaded_skills == []

    # Even an authorized 'supervisor' domain cannot cross roots: the
    # Worker loader only ever resolves vault/skills/.
    state.authorized_domains.add("supervisor")

    result = runtime.load_skill(supervisor_skill_id)

    assert result["ok"] is False
    assert result["error"] == (
        SecondBrainErrorCode.SKILL_NOT_FOUND.value
    )
    assert state.loaded_skills == []


@pytest.mark.asyncio
async def test_task_worker_tool_projection_excludes_supervisor_tool() -> (
    None
):
    class NoExecutionTools:
        async def get_tools(self) -> list[dict[str, Any]]:
            return []

        async def call_tool(
            self,
            name: str,
            arguments: dict[str, Any],
        ) -> str:
            raise AssertionError(name)

    skill_runtime, _ = build_worker_runtime({"research"})

    client = SkillToolClient(
        tools=NoExecutionTools(),
        skill_runtime=skill_runtime,
    )

    names = {
        schema["function"]["name"]
        for schema in await client.get_tools()
    }

    assert names == {"get_skill"}
    assert WORKER_CONTROL_TOOLS == frozenset({"get_skill"})


def test_supervisor_prompt_requires_a_methodology_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planning guidance must ask for discovery, without forcing it."""

    supervisor = build_supervisor(monkeypatch)

    prompt = supervisor.agent.system_prompt

    assert "Before making a non-trivial Task-boundary decision" in prompt
    assert "before delegating the Tasks it should shape" in prompt
    assert (
        "Do not load a methodology merely because a domain is present"
        in prompt
    )
    assert "Loading a methodology earlier is what lets it influence" in (
        prompt
    )
    assert "does not select Worker methodologies" in prompt


def test_supervisor_does_not_receive_get_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    assert "get_supervisor_skill" in supervisor.agent.allowed_tools
    assert "get_skill" not in supervisor.agent.allowed_tools


# F. Path safety


@pytest.mark.parametrize(
    "skill_id",
    [
        None,
        "",
        "   ",
        "supervisor",
        "supervisor.",
        ".coding",
        "supervisor.coding.extra",
        "../supervisor_skills/coding",
        "supervisor.../../coding",
        "supervisor.coding/../research",
        "supervisor.cod ing",
        "supervisor.Code",
        "SUPERVISOR.coding",
        "/supervisor.coding",
        "research.web-search",
        "supervisor.unknown-methodology",
    ],
)
def test_invalid_supervisor_skill_ids_are_rejected(
    skill_id: object,
) -> None:
    runtime = SecondBrainRuntime(VAULT_PATH)

    result = runtime.get_supervisor_skill(skill_id)

    assert isinstance(result, SecondBrainError)
    assert result.code in {
        SecondBrainErrorCode.SKILL_VALIDATION_ERROR,
        SecondBrainErrorCode.SKILL_NOT_FOUND,
    }


# G. Request scope


@pytest.mark.asyncio
async def test_loaded_methodologies_are_request_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_a = build_supervisor(
        monkeypatch,
        request_id="request-a",
    )
    request_b = build_supervisor(
        monkeypatch,
        request_id="request-b",
    )

    assert request_a.second_brain is not request_b.second_brain

    await request_a.control_client.call_tool(
        "get_supervisor_skill",
        {"skill_id": "supervisor.research"},
    )

    # A later step of the same Request still has it.
    await request_a.control_client.call_tool(
        "delegate_task",
        {
            "task": "Count the customers.",
            "domains": ["data"],
        },
    )

    assert request_a.loaded_supervisor_skills == [
        "supervisor.research"
    ]

    # A different Request starts empty.
    assert request_b.loaded_supervisor_skills == []

    # Never persisted into TaskState.
    for state in request_a.task_states.values():
        assert not hasattr(
            state,
            "loaded_supervisor_skills",
        )


# H. Authority invariants


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "methodology",
    [
        "supervisor.research",
        "supervisor.context-allocation",
    ],
)
async def test_loading_a_methodology_changes_no_task_authority(
    monkeypatch: pytest.MonkeyPatch,
    methodology: str,
) -> None:
    supervisor = build_supervisor(monkeypatch)

    delegated = json.loads(
        await supervisor.control_client.call_tool(
            "delegate_task",
            {
                "task": "Count the customers.",
                "domains": ["data"],
            },
        )
    )

    task = supervisor.tasks[delegated["task_id"]]
    state = supervisor.task_states[delegated["task_id"]]

    domains_before = set(state.authorized_domains)
    task_domains_before = list(task.authorized_domains)
    limit_before = state.skill_limit
    loaded_before = list(state.loaded_skills)
    attempt_before = state.attempt
    tools_before = {
        domain: set(
            supervisor.tool_authority.tools_for_domain(domain)
        )
        for domain in domains_before
    }

    await supervisor.control_client.call_tool(
        "get_supervisor_skill",
        {"skill_id": methodology},
    )

    # TaskState authority is untouched.
    assert set(state.authorized_domains) == domains_before
    assert list(task.authorized_domains) == task_domains_before
    assert state.skill_limit == limit_before
    assert state.loaded_skills == loaded_before
    assert state.attempt == attempt_before
    assert state.progress == ""
    assert state.escalation is None

    # Trusted Domain Grants still derive the same tools.
    assert {
        domain: set(
            supervisor.tool_authority.tools_for_domain(domain)
        )
        for domain in domains_before
    } == tools_before

    granted_tools: set[str] = set()

    for tools in tools_before.values():
        granted_tools.update(tools)

    assert "get_supervisor_skill" not in granted_tools
    assert "get_supervisor_skill" not in WORKER_CONTROL_TOOLS


def test_supervisor_skill_tool_has_no_authority_coupling() -> None:
    """The loader's body cannot reference Task or grant authority."""

    source = Path(
        control_tools_module.__file__
    ).read_text(encoding="utf-8")

    start = source.index(
        "async def get_supervisor_skill"
    )
    end = source.index(
        "async def resolve_escalation"
    )

    body = source[start:end]

    for forbidden in (
        "task_states",
        "authorized_domains",
        "allowed_tools",
        "skill_limit",
        "task_executor",
        "tool_authority",
        "escalation",
    ):
        assert forbidden not in body


# I. Research methodology content contract


def research_methodology_content() -> str:
    """Return the normalized research methodology body."""

    loaded = SecondBrainRuntime(VAULT_PATH).get_supervisor_skill(
        "supervisor.research"
    )

    assert not isinstance(loaded, SecondBrainError)

    return " ".join(loaded.projection.content.split()).lower()


def test_research_methodology_separates_subject_from_objective() -> None:
    content = research_methodology_content()

    assert "shared subject is not shared information objective" in content
    assert (
        "same company, product family, technology, event, or subject"
        in content
    )
    assert "independently useful" in content
    assert "acme" in content


def test_research_methodology_rejects_one_answer_as_a_merge_rule() -> None:
    content = research_methodology_content()

    assert "one final answer is not a reason to merge" in content
    assert (
        "number of user-facing answers does not determine" in content
    )

    # The over-aggressive old rule must not return.
    assert "usually need one task" not in content
    assert "several required dimensions but one answer" not in content
    assert "whose evidence overlaps" not in content


def test_research_methodology_keeps_task_and_worker_boundaries() -> None:
    content = research_methodology_content()

    assert "supervisor owns breadth" in content
    assert "final synthesis" in content
    assert (
        "one query, one source, or one search round is not one task"
        in content
    )
    assert (
        "substantially duplicate the same retrieval and reasoning"
        in content
    )
    assert "price and stock status" in content
    assert "mechanically create one task per entity" in content
