"""Generic agent worker for KernelAI."""

from __future__ import annotations

import json
from typing import Any

from openai.types.chat import ChatCompletionMessageParam

from worker.base import BaseWorker
from worker.clients.base import ToolClient
from worker.llm_caller import LLMCaller
from worker.local_llm_caller import LocalLLMCaller


class GenericWorker(BaseWorker):
    """
    Generic KernelAI agent runtime.

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

        self.messages: list[ChatCompletionMessageParam] = []
        self.trace: list[dict[str, Any]] = []

        self.available_tools: list[dict[str, Any]] = []

        self.request_id: str | None = None
        self.iteration = 0

        self.is_done = False
        self.result: dict[str, Any] | None = None

        if local:
            self.llm = LocalLLMCaller()
        else:
            self.llm = LLMCaller()

    def _trace(
        self,
        event: str,
        data: Any = None,
    ) -> None:
        """Record an execution event and optionally print it."""

        self.trace.append(
            {
                "event": event,
                "data": data,
            }
        )

        if not self.verbose:
            return

        print("\n" + "-" * 60)
        print(f"[{event}]")
        print("-" * 60)

        if data is None:
            return

        if isinstance(data, (dict, list)):
            print(
                json.dumps(
                    data,
                    indent=2,
                    default=str,
                )
            )
        else:
            print(data)

    async def start(
        self,
        query: str,
        request_id: str,
    ) -> None:
        """
        Initialize one GenericWorker execution.
        """

        if self.request_id is not None:
            raise RuntimeError(
                "GenericWorker has already been initialized."
            )

        self.request_id = request_id

        self.messages = [
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

        available_tools = await self.tools.get_tools()

        if self.allowed_tools is not None:
            available_tools = [
                tool
                for tool in available_tools
                if tool["function"]["name"] in self.allowed_tools
            ]

        self.available_tools = available_tools

        self._trace(
            "AVAILABLE TOOLS",
            [
                tool["function"]["name"]
                for tool in self.available_tools
            ],
        )

    async def run_step(
        self,
    ) -> bool:
        """
        Execute exactly one agent iteration.

        Returns True when the worker has produced its final result.
        Returns False when additional execution is required.
        """

        if self.request_id is None:
            raise RuntimeError(
                "GenericWorker must be initialized before run_step()."
            )

        if self.is_done:
            return True

        if self.iteration >= self.max_iterations:
            raise RuntimeError(
                "Worker exceeded maximum iterations "
                f"({self.max_iterations}) "
                f"for request {self.request_id!r}."
            )

        self.iteration += 1

        self._trace(
            f"ITERATION {self.iteration} - LLM CALL"
        )

        response = await self.llm.call(
            messages=self.messages,
            tools=self.available_tools,
        )

        if response.content:
            self._trace(
                "ASSISTANT",
                response.content,
            )

        # ---------------------------------------------------------
        # Final response
        # ---------------------------------------------------------

        if not response.tool_calls:
            if response.content and response.content.strip():
                self.result = {
                    "content": response.content,
                }

                self.is_done = True

                self._trace(
                    "FINAL RESPONSE",
                    response.content,
                )

                return True

            raise RuntimeError(
                "LLM returned neither tool calls nor final content "
                f"for request {self.request_id!r}."
            )

        # ---------------------------------------------------------
        # Preserve assistant tool-call message
        # ---------------------------------------------------------

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

        self.messages.append(
            assistant_message
        )

        # ---------------------------------------------------------
        # Execute every tool requested during this turn
        # ---------------------------------------------------------

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

            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                }
            )

        self._trace(
            f"ITERATION {self.iteration} COMPLETE"
        )

        return False

    async def run(
        self,
        query: str,
        request_id: str,
    ) -> dict[str, Any]:
        """
        Execute continuously until the worker produces a final result.
        """

        await self.start(
            query=query,
            request_id=request_id,
        )

        while not self.is_done:
            await self.run_step()

        if self.result is None:
            raise RuntimeError(
                "Worker completed without producing a result."
            )

        return self.result