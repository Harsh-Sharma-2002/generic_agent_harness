"""Generic KernelAI worker."""

from __future__ import annotations

from ast import arguments
from email import message
from typing import Any
from zoneinfo import available_timezones
import json
from worker.base import BaseWorker
from worker.llm_caller import LLMCaller
from openai.types.chat import ChatCompletionMessageParam
from worker.mcp_client import MCPToolClient

class GenericWorker(BaseWorker):
    def __init__(self,llm:LLMCaller,tools:MCPToolClient,max_iterations: int = 10) -> None:
        self.llm = llm
        self.tools = tools
        self.max_iterations = max_iterations

    async def run(self,query: str,request_id: str) -> dict[str:Any]:
        """
        Execute a request until model returns a final resoponse
        or max number of iterations are reached
        """

        messages = list[ChatCompletionMessageParam] = {
                                                       "role":"user",
                                                       "content":query
                                                    }
        available_tools = await self.tools.get_tools()

        for _ in range(self.max_iterations):
            response = await self.llm.call(
                messages = messages,
                tools = available_tools
            )
            # If no tool calls then model is done with reasoning 
            if not response.tool_calls:
                return {
                    "content":response.content
                }


            assistant_message:ChatCompletionMessageParam = {
                "role":"assistant",
                "content":response.content,
                "tool_calls":[
                    {
                    "id":tool_call.id,
                    "type":"function",
                    "function": {
                        "name":tool_call.function.name,
                        "arguments":tool_call.function.arguments,
                        },
                    }
                    for tool_call in response.tool_calls()
                ]
            }

            messages.append(assistant_message)

            # tool call execution

            for tool_call in response.tool_calls():
                try:
                    arguments = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError as exc:
                    raise ValueError("Invalid arguments passed by the llm"
                                     f"for {tool_call.function.name}!r"
                                     f"{tool_call.function.arguments}") from exc

                tool_result = await self.tools.call_tool(
                    name=tool_call.function.name,
                    arguments=arguments
                )

                messages.append({
                    "role":"tool",
                    "tool_call_id":tool_call.id,
                    "content":tool_result
                })
        
        
        # after agent loop ends
        raise RuntimeError(
            f"Worker exceeded maximum iterations "
            f"({self.max_iterations}) for request {request_id!r}."
        )