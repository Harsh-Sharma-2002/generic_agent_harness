"""Deterministic tests for attempt-scoped execution tool budgets.

No LLM, network, MCP server, or real web search is used. Tool clients
are local doubles, so the only behavior under test is the budget
boundary itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from second_brain.models import SecondBrainError, SkillMetadata
from second_brain.runtime import SecondBrainRuntime
from tasks.models import TaskState
from tasks.task import Task, TaskStatus
from tasks.task_executor import TaskExecutor
from tool_grants.registry import TrustedDomainGrants
from worker.clients.budgeted import BudgetedToolClient
from worker.execution_budget import ExecutionBudget
from worker.message import WorkerTaskResult
from worker.skill_runtime import SkillToolClient, WorkerSkillRuntime


VAULT_PATH = Path(__file__).resolve().parents[1] / "vault"

WEB_SEARCH_SKILL = "research.web-search"

TASK_ID = "task-budget"

INSTRUCTION = "What is the current status of the project?"


class RecordingTrace:
    """Minimal tracer recording emitted lifecycle events."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(
        self,
        **kwargs: Any,
    ) -> None:
        self.events.append(kwargs)


class CountingToolClient:
    """Execution tool double that records forwarded calls."""

    def __init__(
        self,
        names: set[str] | None = None,
        *,
        fail: bool = False,
    ) -> None:
        self.names = (
            {"web_search"}
            if names is None
            else set(names)
        )
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append(
            {
                "name": name,
                "arguments": arguments,
            }
        )

        if self.fail:
            raise RuntimeError("underlying tool failed")

        return json.dumps(
            {
                "ok": True,
                "query": arguments.get("query"),
            }
        )


def make_budgeted_client(
    budget: ExecutionBudget,
    tools: CountingToolClient,
    trace: Any = None,
) -> BudgetedToolClient:
    return BudgetedToolClient(
        tools=tools,
        budget=budget,
        request_id="req-budget",
        task_id=TASK_ID,
        attempt=1,
        trace=trace,
    )


def make_state(
    *,
    domains: tuple[str, ...] = ("research",),
    loaded: list[str] | None = None,
) -> TaskState:
    return TaskState(
        task_id=TASK_ID,
        instruction=INSTRUCTION,
        attempt=1,
        authorized_domains=set(domains),
        loaded_skills=list(loaded or []),
    )


def make_worker_skill_runtime(
    budget: ExecutionBudget,
    state: TaskState,
    trace: Any = None,
) -> WorkerSkillRuntime:
    return WorkerSkillRuntime(
        second_brain=SecondBrainRuntime(VAULT_PATH),
        task_state=state,
        attempt=state.attempt,
        request_id="req-budget",
        task_id=TASK_ID,
        execution_budget=budget,
        trace=trace,
    )


# --------------------------------------------------------------------
# Pass-through and enforcement
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlimited_tool_passes_through_without_accounting() -> None:
    budget = ExecutionBudget()
    tools = CountingToolClient()
    client = make_budgeted_client(budget, tools)

    for _ in range(3):
        result = json.loads(
            await client.call_tool(
                "web_search",
                {"query": "q"},
            )
        )

        assert result["ok"] is True

    assert len(tools.calls) == 3
    assert budget.limit_for("web_search") is None
    assert budget.used_for("web_search") == 0


@pytest.mark.asyncio
async def test_third_call_is_rejected_before_underlying_tool() -> None:
    budget = ExecutionBudget()
    budget.register_tool_limits({"web_search": 2})

    tools = CountingToolClient()
    trace = RecordingTrace()
    client = make_budgeted_client(budget, tools, trace)

    first = json.loads(
        await client.call_tool("web_search", {"query": "one"})
    )
    second = json.loads(
        await client.call_tool("web_search", {"query": "two"})
    )
    third = json.loads(
        await client.call_tool("web_search", {"query": "three"})
    )

    assert first["ok"] is True
    assert second["ok"] is True

    # The third call never reaches the underlying execution client.
    assert len(tools.calls) == 2

    assert third["success"] is False
    assert third["error"]["code"] == "tool_budget_exhausted"
    assert third["error"]["limit"] == 2
    assert third["error"]["used"] == 2

    exhausted = [
        event
        for event in trace.events
        if event["event"] == "tool_budget_exhausted"
    ]

    assert len(exhausted) == 1
    assert exhausted[0]["tool"] == "web_search"
    assert exhausted[0]["limit"] == 2


@pytest.mark.asyncio
async def test_failed_underlying_call_consumes_budget() -> None:
    budget = ExecutionBudget()
    budget.register_tool_limits({"web_search": 2})

    tools = CountingToolClient(fail=True)
    client = make_budgeted_client(budget, tools)

    with pytest.raises(RuntimeError):
        await client.call_tool("web_search", {"query": "one"})

    # The reservation happened before execution, so the failure is
    # not refunded.
    assert budget.used_for("web_search") == 1
    assert budget.remaining_for("web_search") == 1

    with pytest.raises(RuntimeError):
        await client.call_tool("web_search", {"query": "two"})

    assert budget.used_for("web_search") == 2

    rejected = json.loads(
        await client.call_tool("web_search", {"query": "three"})
    )

    assert rejected["error"]["code"] == "tool_budget_exhausted"
    assert len(tools.calls) == 2


# --------------------------------------------------------------------
# Limit registration semantics
# --------------------------------------------------------------------


def test_strictest_limit_wins_regardless_of_order() -> None:
    budget = ExecutionBudget()

    budget.register_tool_limits({"web_search": 5})
    budget.register_tool_limits({"web_search": 2})

    assert budget.limit_for("web_search") == 2

    strictest_first = ExecutionBudget()

    strictest_first.register_tool_limits({"web_search": 2})
    strictest_first.register_tool_limits({"web_search": 5})

    assert strictest_first.limit_for("web_search") == 2


def test_registering_limits_does_not_reset_consumed_budget() -> None:
    budget = ExecutionBudget()
    budget.register_tool_limits({"web_search": 2})

    first = budget.reserve_tool("web_search")
    assert first.allowed is True
    assert first.used == 1

    # Loading another methodology (or re-registering the same limit)
    # must not refund consumed capacity.
    budget.register_tool_limits({"web_search": 2})
    budget.register_tool_limits({"web_search": 5})

    second = budget.reserve_tool("web_search")
    assert second.allowed is True
    assert second.used == 2

    third = budget.reserve_tool("web_search")
    assert third.allowed is False
    assert third.used == 2


# --------------------------------------------------------------------
# Methodology metadata -> attempt budget
# --------------------------------------------------------------------


def test_web_search_methodology_declares_two_call_limit() -> None:
    loaded = SecondBrainRuntime(VAULT_PATH).get_skill(
        WEB_SEARCH_SKILL,
        {"research"},
    )

    assert not isinstance(loaded, SecondBrainError)

    metadata: SkillMetadata = loaded.metadata

    assert metadata.tool_limits == {"web_search": 2}


def test_loading_methodology_registers_limit_with_attempt_budget() -> None:
    budget = ExecutionBudget()
    state = make_state()

    skill_runtime = make_worker_skill_runtime(budget, state)

    result = skill_runtime.load_skill(WEB_SEARCH_SKILL)

    assert result["ok"] is True
    assert state.loaded_skills == [WEB_SEARCH_SKILL]
    assert budget.limit_for("web_search") == 2
    assert budget.used_for("web_search") == 0


@pytest.mark.asyncio
async def test_reloading_methodology_does_not_reset_consumed_budget() -> None:
    budget = ExecutionBudget()
    state = make_state()
    trace = RecordingTrace()

    skill_runtime = make_worker_skill_runtime(budget, state, trace)

    assert skill_runtime.load_skill(WEB_SEARCH_SKILL)["ok"] is True

    tools = CountingToolClient()
    client = make_budgeted_client(budget, tools, trace)

    for query in ("one", "two"):
        await client.call_tool("web_search", {"query": query})

    reused = skill_runtime.load_skill(WEB_SEARCH_SKILL)

    assert reused["ok"] is True
    assert reused["reused"] is True
    assert state.loaded_skills == [WEB_SEARCH_SKILL]

    # Reloading neither resets the budget nor re-registers it.
    assert budget.limit_for("web_search") == 2
    assert budget.used_for("web_search") == 2

    rejected = json.loads(
        await client.call_tool("web_search", {"query": "three"})
    )

    assert rejected["error"]["code"] == "tool_budget_exhausted"
    assert len(tools.calls) == 2
    assert any(
        event["event"] == "skill_reused"
        for event in trace.events
    )


def test_new_attempt_starts_with_fresh_budget() -> None:
    first_budget = ExecutionBudget()
    first_state = make_state()

    first_runtime = make_worker_skill_runtime(
        first_budget,
        first_state,
    )

    assert first_runtime.load_skill(WEB_SEARCH_SKILL)["ok"] is True

    for _ in range(2):
        assert first_budget.reserve_tool("web_search").allowed is True

    assert first_budget.remaining_for("web_search") == 0

    # A restarted Task attempt is a new disposable execution scope.
    second_budget = ExecutionBudget()
    second_state = make_state(
        loaded=list(first_state.loaded_skills)
    )

    second_runtime = make_worker_skill_runtime(
        second_budget,
        second_state,
    )

    # The methodology is already loaded semantically, but the budget
    # belongs to the attempt.
    assert second_runtime.load_skill(WEB_SEARCH_SKILL)["reused"] is True
    assert second_budget.used_for("web_search") == 0
    assert second_budget.remaining_for("web_search") is None

    # Re-registering the limit on the new attempt restores enforcement
    # at full capacity.
    second_budget.register_tool_limits({"web_search": 2})

    assert second_budget.reserve_tool("web_search").allowed is True
    assert second_budget.used_for("web_search") == 1


@pytest.mark.asyncio
async def test_budget_never_grants_a_tool() -> None:
    budget = ExecutionBudget()
    budget.register_tool_limits({"new_tool": 5})

    # A limit is a restriction on authority, not a grant: the tool
    # catalog is owned by the execution client.
    tools = CountingToolClient(names={"web_search"})
    client = make_budgeted_client(budget, tools)

    assert budget.limit_for("new_tool") == 5
    assert "new_tool" not in {
        tool["function"]["name"]
        for tool in await client.get_tools()
    }


# --------------------------------------------------------------------
# End-to-end attempt wiring
# --------------------------------------------------------------------


class SkillThenSearchAgent:
    """Worker double that loads the skill, then searches three times."""

    def __init__(self) -> None:
        self.tools: Any = None
        self.skill_result: dict[str, Any] | None = None
        self.search_results: list[dict[str, Any]] = []

    async def run(
        self,
        query: str,
        request_id: str,
        task_id: str | None = None,
    ) -> WorkerTaskResult:
        self.skill_result = json.loads(
            await self.tools.call_tool(
                "get_skill",
                {"skill_id": WEB_SEARCH_SKILL},
            )
        )

        for index in range(3):
            self.search_results.append(
                json.loads(
                    await self.tools.call_tool(
                        "web_search",
                        {"query": f"query-{index}"},
                    )
                )
            )

        return WorkerTaskResult.from_payload(
            {
                "status": "completed",
                "result": {"content": "done"},
                "progress": "",
                "findings": [],
                "artifacts": [],
                "remaining_work": "",
                "escalation": None,
            }
        )


@pytest.mark.asyncio
async def test_task_executor_enforces_methodology_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = SkillThenSearchAgent()
    captured: dict[str, Any] = {}

    def factory(**kwargs: Any) -> SkillThenSearchAgent:
        captured.update(kwargs)
        agent.tools = kwargs["tools"]
        return agent

    monkeypatch.setattr(
        "tasks.task_executor.create_agent",
        factory,
    )

    tools = CountingToolClient()
    trace = RecordingTrace()

    executor = TaskExecutor(
        second_brain=SecondBrainRuntime(VAULT_PATH),
        tool_authority=TrustedDomainGrants(),
        tool_client=tools,
        trace=trace,
        local=True,
    )

    task = Task(
        request_id="req-budget",
        task_id=TASK_ID,
        query=INSTRUCTION,
        authorized_domains=["research"],
        status=TaskStatus.QUEUED,
    )

    outcome = await executor.execute(task, make_state())

    assert outcome.status is TaskStatus.COMPLETED
    assert agent.skill_result is not None
    assert agent.skill_result["ok"] is True

    # The third search is rejected before the underlying client sees
    # it, even though the Worker requested three.
    assert len(tools.calls) == 2
    assert agent.search_results[0]["ok"] is True
    assert agent.search_results[1]["ok"] is True
    assert (
        agent.search_results[2]["error"]["code"]
        == "tool_budget_exhausted"
    )

    # Budget enforcement changes no authority: the attempt still sees
    # exactly its trusted execution tools plus the control capability.
    assert captured["allowed_tools"] == {
        "web_search",
        "get_skill",
    }
