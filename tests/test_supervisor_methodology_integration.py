"""Deterministic integration tests for Supervisor methodology discovery.

The Supervisor's real GenericWorker loop and control tools run here;
only the LLM is scripted. These tests prove that a loaded methodology
body stays in the conversation trajectory, that loading precedes the
delegation it is meant to shape, and that loading remains lazy.

No real model, network, or MCP server is used.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from openai.types.chat import ChatCompletionMessage

from observability.event import RuntimeEvent
from observability.projection import project_request
from supervisor.supervisor import Supervisor
from tasks.request import Request


VAULT_PATH = Path(__file__).resolve().parents[1] / "vault"

REQUEST_ID = "methodology-integration-test"


class RecordingTrace:
    """Minimal tracer that records emitted runtime events."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(
        self,
        **kwargs: Any,
    ) -> None:
        self.events.append(kwargs)


class ScriptedLLM:
    """Deterministic stand-in for the Supervisor's model."""

    def __init__(
        self,
        responses: list[ChatCompletionMessage],
    ) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    async def call(
        self,
        messages: Any,
        tools: Any = None,
        max_tokens: int = 2048,
        response_format: Any = None,
    ) -> ChatCompletionMessage:
        self.calls.append(
            [
                dict(message)
                for message in messages
            ]
        )

        return self.responses.pop(0)


def assistant_with_tools(
    *tools: tuple[str, dict[str, Any]],
) -> ChatCompletionMessage:
    """Build one assistant turn requesting several control tools."""

    return ChatCompletionMessage.model_validate(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call_{index}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(
                            arguments
                        ),
                    },
                }
                for index, (name, arguments) in enumerate(
                    tools
                )
            ],
        }
    )


def final_message(
    content: str,
) -> ChatCompletionMessage:
    return ChatCompletionMessage.model_validate(
        {
            "role": "assistant",
            "content": content,
        }
    )


def build_supervisor(
    monkeypatch: pytest.MonkeyPatch,
    trace: RecordingTrace,
    query: str = "Plan the work.",
) -> Supervisor:
    monkeypatch.setenv(
        "KERNELAI_VAULT_PATH",
        str(VAULT_PATH),
    )

    return Supervisor(
        request=Request(
            request_id=REQUEST_ID,
            query=query,
        ),
        local=True,
        trace=trace,
    )


async def start_supervisor(
    supervisor: Supervisor,
    llm: ScriptedLLM,
) -> None:
    supervisor.agent.llm = llm

    await supervisor.agent.start(
        query=supervisor.request.query,
        request_id=supervisor.request.request_id,
    )


def tool_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        message
        for message in messages
        if message.get("role") == "tool"
    ]


def event_names(
    trace: RecordingTrace,
) -> list[str]:
    return [
        str(event["event"])
        for event in trace.events
    ]


# ============================================================
# 1. Methodology body survives into the next LLM call
# ============================================================


@pytest.mark.asyncio
async def test_methodology_body_reaches_next_llm_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = RecordingTrace()

    supervisor = build_supervisor(
        monkeypatch,
        trace,
    )

    llm = ScriptedLLM(
        [
            assistant_with_tools(
                (
                    "get_supervisor_skill",
                    {"skill_id": "supervisor.research"},
                )
            ),
            final_message("Planned."),
        ]
    )

    await start_supervisor(
        supervisor,
        llm,
    )

    assert not await supervisor.agent.run_step()
    assert await supervisor.agent.run_step()

    # The second model call received the assistant tool call and the
    # actual methodology body as a tool result.
    second_call = llm.calls[1]

    assistant_calls = [
        message
        for message in second_call
        if message.get("role") == "assistant"
        and message.get("tool_calls")
    ]

    assert any(
        tool_call["function"]["name"]
        == "get_supervisor_skill"
        for message in assistant_calls
        for tool_call in message["tool_calls"]
    )

    results = tool_messages(
        second_call
    )

    assert any(
        "# Research Decomposition"
        in (message.get("content") or "")
        for message in results
    )

    assert supervisor.loaded_supervisor_skills == [
        "supervisor.research"
    ]


# ============================================================
# 2. Methodology load precedes delegation
# ============================================================


@pytest.mark.asyncio
async def test_methodology_load_precedes_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = RecordingTrace()

    supervisor = build_supervisor(
        monkeypatch,
        trace,
        query=(
            "Summarize Northwind Robotics' product line, "
            "current availability, and recent announcements."
        ),
    )

    llm = ScriptedLLM(
        [
            assistant_with_tools(
                (
                    "get_supervisor_skill",
                    {"skill_id": "supervisor.research"},
                )
            ),
            assistant_with_tools(
                (
                    "delegate_task",
                    {
                        "task": (
                            "Identify Northwind Robotics' major "
                            "current products and their roles."
                        ),
                        "domains": ["research"],
                    },
                ),
                (
                    "delegate_task",
                    {
                        "task": (
                            "Determine Northwind Robotics' current "
                            "product availability."
                        ),
                        "domains": ["research"],
                    },
                ),
                (
                    "delegate_task",
                    {
                        "task": (
                            "Identify Northwind Robotics' most "
                            "important recent announcements."
                        ),
                        "domains": ["research"],
                    },
                ),
            ),
        ]
    )

    await start_supervisor(
        supervisor,
        llm,
    )

    assert not await supervisor.agent.run_step()
    assert not await supervisor.agent.run_step()

    # The methodology is in context when the Tasks are created.
    assert any(
        "# Research Decomposition"
        in (message.get("content") or "")
        for message in tool_messages(
            llm.calls[1]
        )
    )

    relevant = {
        "supervisor_skill_requested",
        "supervisor_skill_loaded",
        "task_delegated",
    }

    assert [
        name
        for name in event_names(trace)
        if name in relevant
    ] == [
        "supervisor_skill_requested",
        "supervisor_skill_loaded",
        "task_delegated",
        "task_delegated",
        "task_delegated",
    ]

    # All three Tasks belong to one pending batch and none has run.
    assert len(supervisor.pending_tasks) == 3
    assert supervisor.outcomes == {}


# ============================================================
# 3. Multiple methodologies in one Request
# ============================================================


@pytest.mark.asyncio
async def test_multiple_methodologies_reach_next_llm_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = RecordingTrace()

    supervisor = build_supervisor(
        monkeypatch,
        trace,
        query=(
            "Compare a local implementation with current "
            "external best practices."
        ),
    )

    llm = ScriptedLLM(
        [
            assistant_with_tools(
                (
                    "get_supervisor_skill",
                    {"skill_id": "supervisor.coding"},
                )
            ),
            assistant_with_tools(
                (
                    "get_supervisor_skill",
                    {"skill_id": "supervisor.research"},
                )
            ),
            final_message("Planned."),
        ]
    )

    await start_supervisor(
        supervisor,
        llm,
    )

    assert not await supervisor.agent.run_step()
    assert not await supervisor.agent.run_step()
    assert await supervisor.agent.run_step()

    third_call = llm.calls[2]
    context = "\n".join(
        (message.get("content") or "")
        for message in tool_messages(
            third_call
        )
    )

    assert "# Coding Decomposition" in context
    assert "# Research Decomposition" in context

    assert supervisor.loaded_supervisor_skills == [
        "supervisor.coding",
        "supervisor.research",
    ]


# ============================================================
# 4. Lazy loading: a simple Request needs no methodology
# ============================================================


@pytest.mark.asyncio
async def test_simple_request_loads_no_methodology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = RecordingTrace()

    supervisor = build_supervisor(
        monkeypatch,
        trace,
        query="What is the capital of France?",
    )

    supervisor.agent.llm = ScriptedLLM(
        [final_message("Paris.")]
    )

    result = await supervisor.run()

    assert result["content"] == "Paris."
    assert supervisor.loaded_supervisor_skills == []
    assert supervisor.tasks == {}

    assert not any(
        name.startswith("supervisor_skill_")
        for name in event_names(trace)
    )


# ============================================================
# 5. Reuse within one Request
# ============================================================


@pytest.mark.asyncio
async def test_repeated_methodology_load_is_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = RecordingTrace()

    supervisor = build_supervisor(
        monkeypatch,
        trace,
    )

    load_research = (
        "get_supervisor_skill",
        {"skill_id": "supervisor.research"},
    )

    llm = ScriptedLLM(
        [
            assistant_with_tools(load_research),
            assistant_with_tools(load_research),
            final_message("Planned."),
        ]
    )

    await start_supervisor(
        supervisor,
        llm,
    )

    await supervisor.agent.run_step()
    await supervisor.agent.run_step()
    await supervisor.agent.run_step()

    first = json.loads(
        tool_messages(
            llm.calls[1]
        )[-1]["content"]
    )

    second = json.loads(
        tool_messages(
            llm.calls[2]
        )[-1]["content"]
    )

    assert first["reused"] is False
    assert second["reused"] is True

    assert supervisor.loaded_supervisor_skills == [
        "supervisor.research"
    ]

    assert event_names(trace).count(
        "supervisor_skill_loaded"
    ) == 1
    assert event_names(trace).count(
        "supervisor_skill_reused"
    ) == 1


# ============================================================
# 6. Projection exposes loaded methodologies
# ============================================================


def test_projection_exposes_loaded_supervisor_methodologies() -> None:
    timestamp = datetime.now(
        timezone.utc
    )

    def event(
        name: str,
        data: dict[str, Any],
    ) -> RuntimeEvent:
        return RuntimeEvent(
            timestamp=timestamp,
            component="supervisor",
            event=name,
            request_id=REQUEST_ID,
            data=data,
        )

    projection = project_request(
        [
            event("supervisor_started", {}),
            event(
                "supervisor_skill_requested",
                {"skill_id": "supervisor.research"},
            ),
            event(
                "supervisor_skill_loaded",
                {
                    "skill_id": "supervisor.research",
                    "loaded_supervisor_skills": [
                        "supervisor.research"
                    ],
                },
            ),
            event(
                "supervisor_skill_loaded",
                {
                    "skill_id": "supervisor.coding",
                    "loaded_supervisor_skills": [
                        "supervisor.research",
                        "supervisor.coding",
                    ],
                },
            ),
        ]
    )

    assert projection["supervisor"]["loaded_skills"] == [
        "supervisor.research",
        "supervisor.coding",
    ]
