from __future__ import annotations
import json
import os
from typing import Any

from dotenv import load_dotenv
from mcp.client import Client


load_dotenv()

class MCPToolClient():
    """
    Adapter between MCP tools and the OpenAI-compatible tool format
    used by KernelAI's LLM backend.
    """
    def __init__(self) -> None:
        server_url = os.environ.get("MCP_SERVER_URL")

        if not server_url:
            raise ValueError("MCP_SERVER_URL environment variable is not set.")

        self.server_url = server_url

    async def get_tools(self) -> list[dict[str:Any]]:
        """
        Discover tools exposed by the MCP server and convert them
        into OpenAI-compatible function tool definitions.
        """

        async with Client(self.server_url) as client:
            result = await client.list_tools()
            
            return[
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description":tool.description or "",
                        "parameters": tool.input_schema
                    }
                }
                for tool in result.tools
            ]

    async def call_tool(self,name: str,arguments: dict[str:Any]) -> str:
        """
        Execute an MCP tool and return its result as text suitable
        for an OpenAI tool message.
        """
        async with Client(self.server_url) as client:
            result = await client.call_tool(
                name,
                arguments
            )

            if result.is_error:
                raise RuntimeError(f"MCP tool {name!r} failed: {result.content}")
            
            if result.structured_content is not None:
                return json.dumps(
                    result.structured_content,
                    default=str
                )
            
            text_parts = [content.text for content in result.content if hasattr(content,"text")]

            return "\n".join(text_parts)