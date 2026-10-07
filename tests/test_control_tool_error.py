"""Tests for control-tool failure propagation into agent context."""

from __future__ import annotations

import json
from typing import Any

import pytest
from openai.types.chat import ChatCompletionMessage

from worker.clients.control import ControlToolClient
from worker.generic_worker import GenericWorker


TEST_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "failing_control_tool",
        "description": "Control tool that intentionally fails.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}


async def failing_control_tool(
    _: dict[str, Any],
) -> dict[str, Any]:
    """Simulate a recoverable control-tool validation failure."""

    raise ValueError("test control failure")


class FakeLLMCaller:
    """
    Deterministic LLM used to verify that a failed control tool
    is added to conversation context.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.received_messages: list[list[dict[str, Any]]] = []

    async def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
    ) -> ChatCompletionMessage:
        self.calls += 1

        # Preserve the messages exactly as the LLM received them.
        self.received_messages.append(
            [dict(message) for message in messages]
        )

        if self.calls == 1:
            # First model turn requests the intentionally failing tool.
            return ChatCompletionMessage.model_validate(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "test_call_1",
                            "type": "function",
                            "function": {
                                "name": "failing_control_tool",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            )

        # On the second turn, the model should have received the
        # failed tool result in its conversation context.
        return ChatCompletionMessage.model_validate(
            {
                "role": "assistant",
                "content": "Recovered from control tool failure.",
            }
        )


@pytest.mark.asyncio
async def test_control_tool_error_is_added_to_agent_context() -> None:
    control_client = ControlToolClient(
        tools={
            "failing_control_tool": (
                TEST_TOOL_SCHEMA,
                failing_control_tool,
            ),
        }
    )

    worker = GenericWorker(
        tools=control_client,
        system_prompt="Test supervisor.",
        allowed_tools={"failing_control_tool"},
        max_iterations=3,
        verbose=False,
        local=False,
    )

    fake_llm = FakeLLMCaller()
    worker.llm = fake_llm

    result = await worker.run(
        query="Test control tool failure recovery.",
        request_id="control-error-test-001",
    )

    assert result["content"] == (
        "Recovered from control tool failure."
    )

    assert fake_llm.calls == 2

    second_call_messages = fake_llm.received_messages[1]

    tool_messages = [
        message
        for message in second_call_messages
        if message["role"] == "tool"
    ]

    assert len(tool_messages) == 1

    tool_result = json.loads(
        tool_messages[0]["content"]
    )

    assert tool_result == {
        "ok": False,
        "error": "test control failure",
    }

    assert tool_messages[0]["tool_call_id"] == "test_call_1"

