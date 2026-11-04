"""Deterministic tests for Worker lazy skill loading.

No LLM or network is used: the vault is real, or a fake Second Brain
returns canonical LoadedSkill objects.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from second_brain.models import (
    LoadedSkill,
    SecondBrainError,
    SecondBrainErrorCode,
    SkillMetadata,
    SkillProjection,
)
from second_brain.runtime import SecondBrainRuntime
from tasks.models import TaskState
from tool_grants.registry import TrustedDomainGrants
from worker.skill_runtime import (
    GET_SKILL_TOOL_SCHEMA,
    SKILL_LIMIT_REACHED,
    SkillToolClient,
    WorkerSkillRuntime,
)


VAULT_PATH = Path(__file__).resolve().parents[1] / "vault"

CODING_SKILL = "coding.systematic-debugging"

SKILL_IDS = [
    f"coding.skill-{index}"
    for index in range(1, 9)
]


class RecordingTrace:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(
        self,
        **kwargs: Any,
    ) -> None:
        self.events.append(kwargs)


class FakeToolClient:
    """Execution tool client double."""

    def __init__(
        self,
        names: set[str],
    ) -> None:
        self.names = names
        self.calls: list[str] = []

    async def get_tools(
        self,
    ) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": "",
                    "parameters": {},
                },
            }
            for name in sorted(self.names)
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        self.calls.append(name)
        return "execution-result"


class FakeSecondBrain:
    """Second Brain double for limit accounting."""

    def __init__(
        self,
        skills: dict[str, LoadedSkill],
    ) -> None:
        self.skills = skills

    def get_skill(
        self,
        skill_id: str,
        authorized_domains: set[str],
    ) -> LoadedSkill | SecondBrainError:
        loaded = self.skills.get(skill_id)

        if loaded is None:
            return SecondBrainError(
                code=SecondBrainErrorCode.SKILL_NOT_FOUND,
                message=f"Skill '{skill_id}' does not exist.",
            )

        if (
            loaded.metadata.domain
            not in authorized_domains
        ):
            return SecondBrainError(
                code=SecondBrainErrorCode.UNAUTHORIZED_SKILL,
                message=(
                    f"Skill '{skill_id}' belongs to unauthorized "
                    f"domain '{loaded.metadata.domain}'."
                ),
            )

        return loaded


def loaded_skill(
    skill_id: str,
) -> LoadedSkill:
    domain, name = skill_id.split(".", 1)

    return LoadedSkill(
        metadata=SkillMetadata(
            skill_id=skill_id,
            name=name,
            domain=domain,
            description=f"{name} methodology",
            related=(),
        ),
        projection=SkillProjection(
            name=name,
            description=f"{name} methodology",
            content=f"# {name}\n\nMethodology body.",
            related=(),
        ),
    )


def make_state(
    *,
    domains: tuple[str, ...] = ("coding",),
    limit: int = 5,
    loaded: list[str] | None = None,
    attempt: int = 1,
) -> TaskState:
    return TaskState(
        task_id="task_skill",
        instruction="Fix it.",
        attempt=attempt,
        authorized_domains=set(domains),
        skill_limit=limit,
        loaded_skills=list(loaded or []),
    )


def make_runtime(
    second_brain: Any,
    state: TaskState,
    trace: Any = None,
) -> WorkerSkillRuntime:
    return WorkerSkillRuntime(
        second_brain=second_brain,
        task_state=state,
        attempt=state.attempt,
        request_id="req-skill",
        task_id=state.task_id,
        trace=trace,
    )


# --------------------------------------------------------------------
# Loading and bookkeeping
# --------------------------------------------------------------------


def test_authorized_skill_loads() -> None:
    state = make_state()

    runtime = make_runtime(
        SecondBrainRuntime(VAULT_PATH),
        state,
    )

    result = runtime.load_skill(CODING_SKILL)

    assert result["ok"] is True
    assert result["reused"] is False
    assert result["skill"]["skill_id"] == CODING_SKILL
    assert result["skill"]["name"] == "systematic-debugging"
    assert result["skill"]["content"].strip()
    assert "related" in result["skill"]

    assert state.loaded_skills == [CODING_SKILL]
    assert result["loaded_skills"] == 1
    assert result["skill_limit"] == 5


def test_unauthorized_domain_skill_does_not_load() -> None:
    state = make_state(domains=("coding",))

    runtime = make_runtime(
        SecondBrainRuntime(VAULT_PATH),
        state,
    )

    result = runtime.load_skill("research.web-search")

    assert result["ok"] is False
    assert result["error"] == "unauthorized_skill"
    assert "unauthorized domain" in result["message"]
    assert state.loaded_skills == []


def test_nonexistent_skill_does_not_load() -> None:
    state = make_state()

    runtime = make_runtime(
        SecondBrainRuntime(VAULT_PATH),
        state,
    )

    result = runtime.load_skill("coding.does-not-exist")

    assert result["ok"] is False
    assert result["error"] == "skill_not_found"
    assert state.loaded_skills == []


def test_malformed_skill_id_does_not_load() -> None:
    state = make_state()

    runtime = make_runtime(
        SecondBrainRuntime(VAULT_PATH),
        state,
    )

    result = runtime.load_skill("not-a-skill")

    assert result["ok"] is False
    assert result["error"] == "skill_validation_error"
    assert state.loaded_skills == []


def test_empty_skill_id_is_rejected() -> None:
    state = make_state()

    runtime = make_runtime(
        SecondBrainRuntime(VAULT_PATH),
        state,
    )

    for value in (None, "", "   ", 7):
        result = runtime.load_skill(value)

        assert result["ok"] is False
        assert result["error"] == "invalid_skill_id"

    assert state.loaded_skills == []


def test_same_skill_twice_consumes_one_slot() -> None:
    state = make_state()

    runtime = make_runtime(
        SecondBrainRuntime(VAULT_PATH),
        state,
    )

    first = runtime.load_skill(CODING_SKILL)
    second = runtime.load_skill(CODING_SKILL)

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["reused"] is False
    assert second["reused"] is True

    assert state.loaded_skills == [CODING_SKILL]
    assert second["loaded_skills"] == 1
    assert (
        second["skill"]["content"]
        == first["skill"]["content"]
    )


def test_canonical_skill_id_is_used_for_bookkeeping() -> None:
    state = make_state()

    runtime = make_runtime(
        SecondBrainRuntime(VAULT_PATH),
        state,
    )

    result = runtime.load_skill(
        f"  {CODING_SKILL}  "
    )

    assert result["skill"]["skill_id"] == CODING_SKILL
    assert state.loaded_skills == [CODING_SKILL]


# --------------------------------------------------------------------
# Deterministic skill limit
# --------------------------------------------------------------------


def test_five_unique_skills_fit_initial_limit() -> None:
    skills = {
        skill_id: loaded_skill(skill_id)
        for skill_id in SKILL_IDS
    }

    state = make_state(limit=5)

    runtime = make_runtime(
        FakeSecondBrain(skills),
        state,
    )

    for skill_id in SKILL_IDS[:5]:
        assert (
            runtime.load_skill(skill_id)["ok"]
            is True
        )

    assert len(state.loaded_skills) == 5


def test_sixth_unique_skill_is_rejected_at_limit() -> None:
    skills = {
        skill_id: loaded_skill(skill_id)
        for skill_id in SKILL_IDS
    }

    state = make_state(
        limit=5,
        loaded=SKILL_IDS[:5],
    )

    runtime = make_runtime(
        FakeSecondBrain(skills),
        state,
    )

    result = runtime.load_skill(SKILL_IDS[5])

    assert result["ok"] is False
    assert result["error"] == SKILL_LIMIT_REACHED
    assert "Skill limit reached" in result["message"]
    assert state.loaded_skills == SKILL_IDS[:5]


def test_raised_limit_allows_two_more_skills() -> None:
    skills = {
        skill_id: loaded_skill(skill_id)
        for skill_id in SKILL_IDS
    }

    state = make_state(
        limit=7,
        loaded=SKILL_IDS[:5],
    )

    runtime = make_runtime(
        FakeSecondBrain(skills),
        state,
    )

    assert runtime.load_skill(SKILL_IDS[5])["ok"] is True
    assert runtime.load_skill(SKILL_IDS[6])["ok"] is True
    assert len(state.loaded_skills) == 7

    rejected = runtime.load_skill(SKILL_IDS[7])

    assert rejected["error"] == SKILL_LIMIT_REACHED
    assert len(state.loaded_skills) == 7


# --------------------------------------------------------------------
# Authority invariants
# --------------------------------------------------------------------


def test_loaded_skill_grants_no_tool_or_domain() -> None:
    state = make_state()

    runtime = make_runtime(
        SecondBrainRuntime(VAULT_PATH),
        state,
    )

    result = runtime.load_skill(CODING_SKILL)

    # Domains are unchanged, no tool authority appears anywhere, and
    # the trusted registry is untouched.
    assert state.authorized_domains == {"coding"}
    assert not hasattr(state, "allowed_tools")
    assert "tools" not in result["skill"]
    assert "allowed_tools" not in result

    assert (
        TrustedDomainGrants().tools_for_domain("coding")
        == frozenset({"shell"})
    )


def test_get_skill_schema_requires_only_skill_id() -> None:
    function = GET_SKILL_TOOL_SCHEMA["function"]

    assert function["name"] == "get_skill"

    parameters = function["parameters"]

    assert parameters["required"] == ["skill_id"]
    assert set(parameters["properties"]) == {"skill_id"}
    assert parameters["additionalProperties"] is False


# --------------------------------------------------------------------
# Control client and observability
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skill_client_exposes_control_and_execution_tools() -> None:
    execution = FakeToolClient({"shell"})
    state = make_state()

    client = SkillToolClient(
        tools=execution,
        skill_runtime=make_runtime(
            SecondBrainRuntime(VAULT_PATH),
            state,
        ),
    )

    names = {
        tool["function"]["name"]
        for tool in await client.get_tools()
    }

    assert names == {"shell", "get_skill"}

    result = json.loads(
        await client.call_tool(
            "get_skill",
            {"skill_id": CODING_SKILL},
        )
    )

    assert result["ok"] is True
    assert state.loaded_skills == [CODING_SKILL]

    # Execution tools are delegated unchanged.
    assert (
        await client.call_tool(
            "shell",
            {"command": "ls"},
        )
        == "execution-result"
    )

    assert execution.calls == ["shell"]


def test_skill_events_do_not_log_content() -> None:
    trace = RecordingTrace()

    skills = {
        skill_id: loaded_skill(skill_id)
        for skill_id in SKILL_IDS
    }

    state = make_state(limit=6)

    runtime = make_runtime(
        FakeSecondBrain(skills),
        state,
        trace,
    )

    runtime.load_skill(SKILL_IDS[0])
    runtime.load_skill(SKILL_IDS[0])

    for skill_id in SKILL_IDS[1:6]:
        runtime.load_skill(skill_id)

    runtime.load_skill(SKILL_IDS[6])

    events = [
        event["event"]
        for event in trace.events
    ]

    assert "skill_loaded" in events
    assert "skill_reused" in events
    assert "skill_limit_reached" in events

    # Full skill bodies are never traced.
    assert (
        "Methodology body."
        not in json.dumps(trace.events)
    )

    loaded_event = next(
        event
        for event in trace.events
        if event["event"] == "skill_loaded"
    )

    assert loaded_event["attempt"] == 1
    assert loaded_event["loaded_skills"] == 1
    assert loaded_event["skill_limit"] == 6
    assert loaded_event["domain"] == "coding"
