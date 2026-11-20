"""Dispatch authorization for model-originated tool calls.

GenericWorker converts untrusted model output into runtime actions, so
``allowed_tools`` must be enforced immediately before dispatch. The
filtered schema list shown to the model is not a security boundary:
a model can emit any tool name.

No LLM, MCP server, or network is used.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from openai.types.chat import (
    ChatCompletionMessage,
    ChatCompletionMessageToolCall,
)

from worker.generic_worker import GenericWorker


class RecordingTrace:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(
        self,
        **kwargs: Any,
    ) -> None:
        self.events.append(kwargs)


class ScriptedLLM:
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
            }
        )

        return self.responses.pop(0)


class RecordingToolClient:
    """Records every call that actually reaches the tool layer."""

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
                    "description": "",
                    "parameters": {},
                },
            }
            for name in (
                "get_skill",
                "web_search",
                "shell",
            )
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        self.calls.append((name, arguments))

        return json.dumps({"ok": True})


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


def make_worker(
    responses: list[ChatCompletionMessage],
    *,
    allowed_tools: set[str] | None,
    trace: RecordingTrace | None = None,
) -> GenericWorker:
    worker = GenericWorker(
        tools=RecordingToolClient(),
        system_prompt="test system prompt",
        allowed_tools=allowed_tools,
        max_iterations=4,
        local=True,
        trace=trace,
    )

    worker.llm = ScriptedLLM(responses)

    return worker


@pytest.mark.asyncio
async def test_unauthorized_tool_is_rejected_before_dispatch() -> None:
    trace = RecordingTrace()

    worker = make_worker(
        [
            assistant(
                tool_calls=[
                    tool_call(
                        "call-1",
                        "shell",
                        {"command": "echo hi"},
                    )
                ]
            ),
            assistant(content="Recovered without the tool."),
        ],
        allowed_tools={"get_skill", "web_search"},
        trace=trace,
    )

    result = await worker.run(
        query="Research this.",
        request_id="req-tool-authority",
        task_id="task-tool-authority",
    )

    # The Worker recovers on the next iteration.
    assert result == {
        "content": "Recovered without the tool."
    }

    # The unauthorized tool never reached the tool layer.
    assert worker.tools.calls == []

    tool_messages = [
        message
        for message in worker.messages
        if message.get("role") == "tool"
    ]

    assert len(tool_messages) == 1

    bounded = json.loads(
        tool_messages[0]["content"]
    )

    assert bounded["ok"] is False
    assert "not authorized" in bounded["error"]
    assert "echo hi" not in tool_messages[0]["content"]

    # The model sees the rejection before its next turn.
    assert worker.llm.calls[1]["messages"][-1]["role"] == "tool"

    failed = [
        event
        for event in trace.events
        if event["event"] == "tool_call_failed"
    ]

    assert len(failed) == 1
    assert failed[0]["error"] == "unauthorized_tool"
    assert failed[0]["tool_name"] == "shell"
    assert "arguments" not in failed[0]
    assert "arguments_raw" not in failed[0]


@pytest.mark.asyncio
async def test_authorized_tool_still_dispatches() -> None:
    worker = make_worker(
        [
            assistant(
                tool_calls=[
                    tool_call(
                        "call-1",
                        "web_search",
                        {"query": "KernelAI"},
                    )
                ]
            ),
            assistant(content="Done."),
        ],
        allowed_tools={"get_skill", "web_search"},
    )

    result = await worker.run(
        query="Research this.",
        request_id="req-tool-authorized",
    )

    assert result == {"content": "Done."}
    assert worker.tools.calls == [
        ("web_search", {"query": "KernelAI"})
    ]


@pytest.mark.asyncio
async def test_mixed_turn_dispatches_only_authorized_calls() -> None:
    worker = make_worker(
        [
            assistant(
                tool_calls=[
                    tool_call(
                        "call-1",
                        "shell",
                        {"command": "echo hi"},
                    ),
                    tool_call(
                        "call-2",
                        "web_search",
                        {"query": "KernelAI"},
                    ),
                ]
            ),
            assistant(content="Done."),
        ],
        allowed_tools={"get_skill", "web_search"},
    )

    await worker.run(
        query="Research this.",
        request_id="req-tool-mixed",
    )

    assert worker.tools.calls == [
        ("web_search", {"query": "KernelAI"})
    ]


@pytest.mark.asyncio
async def test_unrestricted_worker_keeps_none_semantics() -> None:
    # allowed_tools=None remains "unrestricted" for non-Task Workers.
    worker = make_worker(
        [
            assistant(
                tool_calls=[
                    tool_call(
                        "call-1",
                        "shell",
                        {"command": "echo hi"},
                    )
                ]
            ),
            assistant(content="Done."),
        ],
        allowed_tools=None,
    )

    await worker.run(
        query="Use any tool.",
        request_id="req-tool-unrestricted",
    )

    assert worker.tools.calls == [
        ("shell", {"command": "echo hi"})
    ]
