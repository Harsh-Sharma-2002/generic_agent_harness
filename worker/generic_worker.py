"""Generic agent worker for KernelAI."""

from __future__ import annotations

import json
from typing import Any

from openai.types.chat import ChatCompletionMessageParam

from worker.base import BaseWorker
from worker.llm_caller import LLMCaller
from worker.local_llm_caller import LocalLLMCaller
from worker.clients.base import ToolClient


class GenericWorker(BaseWorker):
    """
    Generic KernelAI worker.

    Behavior is supplied through a system prompt.
    Tools are provided through the configured tool client.
    """

    def __init__(
        self,
        tools: ToolClient,
        system_prompt: str,
        allowed_tools: set[str] | None = None,
        max_iterations: int = 10,
        verbose: bool = False,
        local: bool = False,
    ) -> None:
        self.tools = tools
        self.system_prompt = system_prompt
        self.allowed_tools = allowed_tools
        self.max_iterations = max_iterations
        self.verbose = verbose

        if local:
            self.llm = LocalLLMCaller()
        else:
            self.llm = LLMCaller()

    def _trace(
        self,
        label: str,
        value: Any = None,
    ) -> None:
        """Print execution information during verbose runs."""

        if not self.verbose:
            return

        print("\n" + "-" * 60)
        print(f"[{label}]")
        print("-" * 60)

        if value is None:
            return

        if isinstance(value, (dict, list)):
            print(
                json.dumps(
                    value,
                    indent=2,
                    default=str,
                )
            )
        else:
            print(value)

    async def run(
        self,
        query: str,
        request_id: str,
    ) -> dict[str, Any]:
        """
        Execute a request until the model produces a final response
        or the maximum number of iterations is reached.
        """

        messages: list[ChatCompletionMessageParam] = [
            {
                "role": "system",
                "content": self.system_prompt,
            },
            {
                "role": "user",
                "content": query,
            },
        ]

        self._trace(
            "REQUEST",
            {
                "request_id": request_id,
                "query": query,
            },
        )

        # Discover all tools exposed by MCP.
        available_tools = await self.tools.get_tools()

        # Restrict this worker instance to the tools assigned to it.
        if self.allowed_tools is not None:
            available_tools = [
                tool
                for tool in available_tools
                if tool["function"]["name"] in self.allowed_tools
            ]

        self._trace(
            "AVAILABLE TOOLS",
            [
                tool["function"]["name"]
                for tool in available_tools
            ],
        )

        # Main agent loop.
        for iteration in range(
            1,
            self.max_iterations + 1,
        ):
            self._trace(
                f"ITERATION {iteration} - LLM CALL"
            )

            response = await self.llm.call(
                messages=messages,
                tools=available_tools,
            )

            if response.content:
                self._trace(
                    "ASSISTANT",
                    response.content,
                )

            # -----------------------------------------------------
            # Final response
            # -----------------------------------------------------

            if not response.tool_calls:
                if response.content and response.content.strip():
                    self._trace(
                        "FINAL RESPONSE",
                        response.content,
                    )

                    return {
                        "content": response.content,
                    }

                raise RuntimeError(
                    "LLM returned neither tool calls nor final content "
                    f"for request {request_id!r}."
                )

            # -----------------------------------------------------
            # Preserve assistant tool-call message
            # -----------------------------------------------------

            assistant_message: ChatCompletionMessageParam = {
                "role": "assistant",
                "content": response.content,
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                    for tool_call in response.tool_calls
                ],
            }

            messages.append(assistant_message)

            # -----------------------------------------------------
            # Execute every tool requested by this LLM turn
            # -----------------------------------------------------

            for tool_call in response.tool_calls:
                try:
                    arguments = json.loads(
                        tool_call.function.arguments
                    )

                    self._trace(
                        "TOOL CALL",
                        {
                            "tool": tool_call.function.name,
                            "arguments": arguments,
                        },
                    )

                    tool_result = await self.tools.call_tool(
                        name=tool_call.function.name,
                        arguments=arguments,
                    )

                except json.JSONDecodeError as exc:
                    tool_result = json.dumps(
                        {
                            "ok": False,
                            "error": (
                                "Invalid JSON tool arguments: "
                                f"{exc.msg}"
                            ),
                        }
                    )

                except (ValueError, RuntimeError) as exc:
                    tool_result = json.dumps(
                        {
                            "ok": False,
                            "error": str(exc),
                        }
                    )

                self._trace(
                    "TOOL RESULT",
                    tool_result,
                )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result,
                    }
                )

            self._trace(
                f"ITERATION {iteration} COMPLETE"
            )

        raise RuntimeError(
            "Worker exceeded maximum iterations "
            f"({self.max_iterations}) "
            f"for request {request_id!r}."
        )