"""Generic agent worker for KernelAI."""

from __future__ import annotations

import json
from typing import Any

from openai.types.chat import ChatCompletionMessageParam

from worker.base import BaseWorker
from worker.llm_caller import LLMCaller
from worker.mcp_client import MCPToolClient


class GenericWorker(BaseWorker):
    """
    Generic KernelAI worker.

    The worker receives its behavior through a system prompt and its
    capabilities dynamically through MCP.

    It executes the standard agent loop:

        LLM -> tool call(s) -> tool result(s) -> LLM -> ...

    until the model produces a final response.
    """

    def __init__(
        self,
        llm: LLMCaller,
        tools: MCPToolClient,
        system_prompt: str,
        max_iterations: int = 10,
        verbose: bool = False,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.verbose = verbose

    def _trace(
        self,
        label: str,
        value: Any = None,
    ) -> None:
        """Print worker execution information during verbose runs."""

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
        Execute a request until the model returns a final response
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

        available_tools = await self.tools.get_tools()

        self._trace(
            "AVAILABLE TOOLS",
            [
                tool["function"]["name"]
                for tool in available_tools
            ],
        )

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

          
            # Final response
            

            if not response.tool_calls:
                if response.content and response.content.strip():
                    return {
                        "content": response.content,
                        }

                raise RuntimeError(
                    f"LLM returned neither tool calls nor final content "
                    f"for request {request_id!r}."
                    )

           
            # Preserve assistant tool-call message
          

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

            
            # Execute requested tools
           

            for tool_call in response.tool_calls:
                try:
                    arguments = json.loads(
                        tool_call.function.arguments
                    )

                except json.JSONDecodeError as exc:
                    raise ValueError(
                        "Invalid tool arguments returned by LLM "
                        f"for {tool_call.function.name!r}: "
                        f"{tool_call.function.arguments}"
                    ) from exc

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

                self._trace(
                    "TOOL RESULT",
                    tool_result,
                )

                
                # Add tool result to conversation history
                

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

        # Safety limit reached
       

        raise RuntimeError(
            "Worker exceeded maximum iterations "
            f"({self.max_iterations}) "
            f"for request {request_id!r}."
        )