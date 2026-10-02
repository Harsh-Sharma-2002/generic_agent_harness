"""Composite tool client used by the KernelAI orchestrator."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable


from worker.mcp_client import MCPToolClient

LocalToolHandler = Callable[
    [dict[str:Any]],
    Awaitable[Any]
]

class CompositeToolClient:
    """
    Tool client used by the KernelAI orchestrator.

    Combines:

    1. Normal tools discovered from the MCP server.
    2. Privileged local runtime tools available only to the orchestrator.

    Normal GenericWorkers receive MCPToolClient directly and therefore
    never receive these privileged runtime capabilities.
    """

    def __init__(self,local_tools:dict[str,tuple[dict[str,Any],LocalToolHandler]]) -> None:
                 self.mcp = MCPToolClient()
                 self.local_tools = local_tools

    async def get_tools(self) -> list[dict[str,Any]]:
        """
        Return both MCP tools and privileged local runtime tools
        in OpenAI-compatible function-tool format.
        """

        mcp_tools =  await self.mcp.get_tools()

        local_tool_schemas = [schema for schema,_ in self.local_tools.values()]

        mcp_names = {
            tool["function"]["name"]
            for tool in mcp_tools
        }

        local_names = set(self.local_tools)

        duplicates = mcp_names & local_names

        if duplicates:
            raise ValueError(
                    "Duplicate tool names between MCP and local "
                    f"runtime tools: {sorted(duplicates)}"
            )

        return mcp_tools + local_tool_schemas

    async def call_tool(self,name:str,arguments:dict[str,Any]) -> str:
        """
        Execute a tool.

        Privileged local tools are executed inside the KernelAI
        runtime. All other calls are forwarded to the MCP server.
        """
        if name in self.local_tools:
            _, handler = self.local_tools[name]

            result = await handler(arguments)

            if isinstance(result,str):
                return result

            return json.dumps(
                result,
                default=str
            )
        
        return await self.mcp.call_tool(
                                name=name,
                                arguments=arguments
            )


            