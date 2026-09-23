import pytest

from mcp.client import Client


MCP_URL = "http://localhost:8001/mcp"


@pytest.mark.asyncio
async def test_mcp_tools_and_web_search():
    async with Client(MCP_URL) as client:

        # 1. Verify both tools are advertised by the MCP server.
        tool_result = await client.list_tools()

        tool_names = {tool.name for tool in tool_result.tools}

        assert "web_search" in tool_names
        assert "sql_executor" in tool_names

        # 2. Actually execute only web_search.
        result = await client.call_tool(
            "web_search",
            {
                "query": "NVIDIA",
                "max_results": 3,
            },
        )

        assert result is not None

        print("\nWeb search result:")
        print(result)
