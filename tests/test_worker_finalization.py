"""Deterministic tests for Task Worker structured finalization.

No LLM, MCP server, or network is used: the caller and tool client are
scripted doubles. These tests pin the boundary that production E2E
exposed: a Task Worker keeps normal tool calling, then packages its
completed attempt into exactly one canonical WorkerTaskResult through
one tool-free finalization call.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from openai.types.chat import (
    ChatCompletionMessage,
    ChatCompletionMessageToolCall,
)

from tasks.models import EscalationKind, EscalationRequest
from worker.factory import create_agent
from worker.generic_worker import GenericWorker
from worker.message import (
    InvalidWorkerResult,
    WORKER_TASK_RESULT_RESPONSE_FORMAT,
    WORKER_TASK_RESULT_SCHEMA,
    WorkerIterationExhausted,
    WorkerTaskResult,
)


# --------------------------------------------------------------------
# Doubles and helpers
# --------------------------------------------------------------------


class RecordingTrace:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(
        self,
        **kwargs: Any,
    ) -> None:
        self.events.append(kwargs)


class FakeLLMCaller:
    """Scripted caller that records tools and response format."""

    def __init__(
        self,
        responses: list[ChatCompletionMessage],
    ) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def call(
        self,
        messages: Any,
        tools: Any = None,
        max_tokens: int = 2048,
        response_format: Any = None,
    ) -> ChatCompletionMessage:
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "max_tokens": max_tokens,
                "response_format": response_format,
            }
        )

        return self.responses.pop(0)


class FakeToolClient:
    """Minimal tool client with get_skill and one execution tool."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get_tools(
        self,
    ) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": name,
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            }
            for name in ("get_skill", "shell")
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        self.calls.append((name, arguments))

        return json.dumps(
            {
                "ok": True,
                "skill": {
                    "skill_id": arguments.get("skill_id"),
                    "name": "codebase-inspection",
                },
            }
        )


def assistant(
    content: str | None = None,
    tool_calls: list[ChatCompletionMessageToolCall] | None = None,
) -> ChatCompletionMessage:
    payload: dict[str, Any] = {
        "role": "assistant",
        "content": content,
    }

    if tool_calls:
        payload["tool_calls"] = tool_calls

    return ChatCompletionMessage.model_validate(payload)


def tool_call(
    call_id: str,
    name: str,
    arguments: dict[str, Any],
) -> ChatCompletionMessageToolCall:
    return ChatCompletionMessageToolCall.model_validate(
        {
            "id": call_id,
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(arguments),
            },
        }
    )


def payload(
    **overrides: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": "completed",
        "result": {"content": "done"},
        "progress": "",
        "findings": [],
        "artifacts": [],
        "remaining_work": "",
        "escalation": None,
    }

    base.update(overrides)

    return base


def make_worker(
    responses: list[ChatCompletionMessage],
    *,
    trace: RecordingTrace | None = None,
    final_response_format: Any = (
        WORKER_TASK_RESULT_RESPONSE_FORMAT
    ),
) -> GenericWorker:
    worker = GenericWorker(
        tools=FakeToolClient(),
        system_prompt="test system prompt",
        allowed_tools={"get_skill", "shell"},
        max_iterations=6,
        local=True,
        final_response_format=final_response_format,
        trace=trace,
    )

    worker.llm = FakeLLMCaller(responses)

    return worker


# --------------------------------------------------------------------
# Finalization boundary
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_run_before_one_structured_finalization_call() -> None:
    trace = RecordingTrace()

    draft = "I inspected the repository and found one issue."

    worker = make_worker(
        [
            assistant(
                tool_calls=[
                    tool_call(
                        "call-1",
                        "get_skill",
                        {
                            "skill_id": (
                                "coding.codebase-inspection"
                            )
                        },
                    )
                ]
            ),
            assistant(content=draft),
            assistant(
                content=json.dumps(
                    payload(
                        result={"content": "found issue"},
                    )
                )
            ),
        ],
        trace=trace,
    )

    result = await worker.run(
        query="Inspect.",
        request_id="req-finalization",
    )

    assert isinstance(result, WorkerTaskResult)
    assert result.status == "completed"
    assert result.result == {"content": "found issue"}

    # Intermediate get_skill executed normally.
    assert worker.tools.calls == [
        (
            "get_skill",
            {"skill_id": "coding.codebase-inspection"},
        )
    ]

    calls = worker.llm.calls

    # Two working iterations plus exactly one finalization call.
    assert len(calls) == 3

    # Working iterations keep their tools.
    assert calls[0]["tools"] is not None
    assert calls[1]["tools"] is not None

    # Finalization exposes zero tools and the canonical format.
    assert calls[2]["tools"] is None
    assert (
        calls[2]["response_format"]
        is WORKER_TASK_RESULT_RESPONSE_FORMAT
    )

    # The draft final answer is preserved for packaging.
    final_messages = calls[2]["messages"]

    assistant_contents = [
        message["content"]
        for message in final_messages
        if message["role"] == "assistant"
    ]

    assert assistant_contents[-1] == draft

    instruction = final_messages[-1]["content"]

    assert '"status"' in instruction
    assert "Do not call any tools." in instruction

    events = [
        event["event"]
        for event in trace.events
    ]

    assert events.count("worker_finalization_started") == 1
    assert events.count("worker_finalization_completed") == 1
    assert "worker_finalization_failed" not in events
    assert "worker_completed" in events

    completed = next(
        event
        for event in trace.events
        if event["event"] == "worker_finalization_completed"
    )

    assert completed["status"] == "completed"
    assert completed["escalation"] is None


@pytest.mark.parametrize(
    ("escalation", "kind", "field", "value"),
    [
        (
            {
                "kind": "domain_required",
                "reason": "Needs sources.",
                "requested_domain": "research",
            },
            EscalationKind.DOMAIN_REQUIRED,
            "requested_domain",
            "research",
        ),
        (
            {
                "kind": "tool_required",
                "reason": "Needs a shell.",
                "requested_tool": "shell",
            },
            EscalationKind.TOOL_REQUIRED,
            "requested_tool",
            "shell",
        ),
        (
            {
                "kind": "skill_limit_exceeded",
                "reason": "Needs another methodology.",
                "requested_skill_limit": 7,
            },
            EscalationKind.SKILL_LIMIT_EXCEEDED,
            "requested_skill_limit",
            7,
        ),
    ],
)
@pytest.mark.asyncio
async def test_blocked_finalization_produces_canonical_escalation(
    escalation: dict[str, Any],
    kind: EscalationKind,
    field: str,
    value: Any,
) -> None:
    worker = make_worker(
        [
            assistant(content="I cannot continue."),
            assistant(
                content=json.dumps(
                    payload(
                        status="blocked",
                        result=None,
                        escalation=escalation,
                    )
                )
            ),
        ]
    )

    result = await worker.run(
        query="Do it.",
        request_id="req-blocked",
    )

    assert result.status == "blocked"
    assert isinstance(
        result.escalation,
        EscalationRequest,
    )
    assert result.escalation.kind is kind
    assert getattr(result.escalation, field) == value


@pytest.mark.parametrize(
    ("name", "final_content"),
    [
        (
            "completed-with-escalation",
            json.dumps(
                payload(
                    escalation={
                        "kind": "domain_required",
                        "reason": "Needs sources.",
                        "requested_domain": "research",
                    }
                )
            ),
        ),
        (
            "blocked-without-escalation",
            json.dumps(
                payload(
                    status="blocked",
                    result=None,
                    escalation=None,
                )
            ),
        ),
        (
            "unknown-status",
            json.dumps(payload(status="partial")),
        ),
        (
            "unknown-escalation-kind",
            json.dumps(
                payload(
                    status="blocked",
                    result=None,
                    escalation={
                        "kind": "browser_required",
                        "reason": "Needs a browser.",
                    },
                )
            ),
        ),
        (
            "wrong-escalation-field",
            json.dumps(
                payload(
                    status="blocked",
                    result=None,
                    escalation={
                        "kind": "tool_required",
                        "reason": "Needs research.",
                        "requested_domain": "research",
                    },
                )
            ),
        ),
        (
            "unexpected-field",
            json.dumps(payload(unexpected=True)),
        ),
        (
            "malformed-json",
            "I completed the task successfully.",
        ),
        (
            "non-object-json",
            json.dumps(["completed"]),
        ),
    ],
)
@pytest.mark.asyncio
async def test_invalid_finalization_fails_explicitly(
    name: str,
    final_content: str,
) -> None:
    trace = RecordingTrace()

    worker = make_worker(
        [
            assistant(content="Draft answer."),
            assistant(content=final_content),
        ],
        trace=trace,
    )

    with pytest.raises(InvalidWorkerResult):
        await worker.run(
            query="Do it.",
            request_id=f"req-{name}",
        )

    # Exactly one finalization attempt was made.
    assert len(worker.llm.calls) == 2
    assert worker.llm.calls[1]["tools"] is None

    events = [
        event["event"]
        for event in trace.events
    ]

    assert events.count("worker_finalization_started") == 1
    assert events.count("worker_finalization_failed") == 1
    assert "worker_finalization_completed" not in events
    assert "worker_completed" not in events


# --------------------------------------------------------------------
# Optional contract: Supervisor / Learning Supervisor unchanged
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_without_contract_still_returns_plain_content() -> None:
    worker = make_worker(
        [assistant(content="Just the answer.")],
        final_response_format=None,
    )

    result = await worker.run(
        query="Do it.",
        request_id="req-plain",
    )

    assert result == {"content": "Just the answer."}

    # No finalization call: existing Supervisor behavior.
    assert len(worker.llm.calls) == 1
    assert worker.llm.calls[0]["response_format"] is None


def test_finalization_contract_is_opt_in() -> None:
    def make_agent(**kwargs: Any) -> Any:
        return create_agent(
            tools=FakeToolClient(),
            system_prompt="system",
            allowed_tools={"get_skill"},
            local=True,
            **kwargs,
        )

    supervisor_agent = make_agent(
        worker_role="supervisor"
    )
    learning_agent = make_agent(
        worker_role="learning_supervisor"
    )
    task_agent = make_agent(
        worker_role="task",
        final_response_format=(
            WORKER_TASK_RESULT_RESPONSE_FORMAT
        ),
    )

    assert supervisor_agent.final_response_format is None
    assert learning_agent.final_response_format is None
    assert (
        task_agent.final_response_format
        is WORKER_TASK_RESULT_RESPONSE_FORMAT
    )


# --------------------------------------------------------------------
# Canonical schema
# --------------------------------------------------------------------


def test_response_format_derives_from_canonical_model() -> None:
    json_schema = WORKER_TASK_RESULT_RESPONSE_FORMAT[
        "json_schema"
    ]

    assert json_schema["name"] == "worker_task_result"
    assert json_schema["schema"] is WORKER_TASK_RESULT_SCHEMA

    schema = json_schema["schema"]

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(
        WorkerTaskResult.model_fields
    )
    assert schema["properties"]["status"]["enum"] == [
        "completed",
        "blocked",
    ]

    escalation = schema["$defs"]["EscalationRequest"]

    assert escalation["additionalProperties"] is False
    assert set(escalation["required"]) == {
        "kind",
        "reason",
        "requested_domain",
        "requested_tool",
        "requested_skill_limit",
    }

    kinds = schema["$defs"]["EscalationKind"]["enum"]

    assert set(kinds) == {
        "domain_required",
        "tool_required",
        "skill_limit_exceeded",
    }


# --------------------------------------------------------------------
# Execution-budget boundary
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iteration_budget_exhaustion_is_not_completion() -> None:
    trace = RecordingTrace()

    worker = make_worker(
        [
            assistant(
                tool_calls=[
                    tool_call(
                        "call-1",
                        "shell",
                        {"command": "ls"},
                    )
                ]
            ),
            assistant(
                tool_calls=[
                    tool_call(
                        "call-2",
                        "shell",
                        {"command": "pwd"},
                    )
                ]
            ),
            assistant(
                content=json.dumps(
                    payload(
                        status="completed",
                        result={"content": "partial"},
                        progress="Inspected the layout.",
                        findings=["Found X"],
                    )
                )
            ),
        ],
        trace=trace,
    )

    worker.max_iterations = 2

    # The finalizer said "completed"; the runtime rejects that fact.
    with pytest.raises(WorkerIterationExhausted) as excinfo:
        await worker.run(
            query="Inspect.",
            request_id="req-budget",
        )

    partial = excinfo.value.result

    assert partial is not None
    assert partial.status == "completed"
    assert partial.progress == "Inspected the layout."
    assert partial.findings == ["Found X"]

    calls = worker.llm.calls

    # Two execution iterations plus exactly one finalization call.
    assert len(calls) == 3
    assert calls[0]["tools"] is not None
    assert calls[1]["tools"] is not None

    assert calls[2]["tools"] is None
    assert (
        calls[2]["response_format"]
        is WORKER_TASK_RESULT_RESPONSE_FORMAT
    )

    events = [event["event"] for event in trace.events]

    assert "worker_iteration_limit_reached" in events
    assert "worker_finalization_started" in events
    assert "worker_finalization_completed" in events
    assert "worker_iteration_exhausted" in events

    instruction = calls[2]["messages"][-1]["content"]

    assert "execution budget is exhausted" in instruction
    assert "was NOT completed" in instruction


@pytest.mark.asyncio
async def test_natural_completion_on_final_allowed_iteration() -> None:
    worker = make_worker(
        [
            assistant(content="I fixed it."),
            assistant(
                content=json.dumps(
                    payload(result={"content": "done"})
                )
            ),
        ],
    )

    worker.max_iterations = 1

    result = await worker.run(
        query="Do it.",
        request_id="req-last-iteration",
    )

    # Iteration 1 == max_iterations and the Worker produced its final
    # answer: this is a natural completion, not exhaustion.
    assert isinstance(result, WorkerTaskResult)
    assert result.status == "completed"
    assert result.result == {"content": "done"}

    # One execution call plus one finalization call.
    assert len(worker.llm.calls) == 2


@pytest.mark.asyncio
async def test_iteration_budget_without_contract_still_fails() -> None:
    worker = make_worker(
        [
            assistant(
                tool_calls=[
                    tool_call(
                        "call-1",
                        "shell",
                        {"command": "ls"},
                    )
                ]
            )
        ],
        final_response_format=None,
    )

    worker.max_iterations = 1

    # Workers without a finalization contract keep the old failure.
    with pytest.raises(RuntimeError):
        await worker.run(
            query="Inspect.",
            request_id="req-budget-fail",
        )
