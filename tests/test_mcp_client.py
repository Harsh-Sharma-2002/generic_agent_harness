import json

import pytest

from worker.clients.mcp import MCPToolClient


@pytest.mark.asyncio
async def test_get_tools_returns_openai_format():
    client = MCPToolClient()

    tools = await client.get_tools()

    assert tools

    tool_names = {
        tool["function"]["name"]
        for tool in tools
    }

    assert "web_search" in tool_names
    assert "sql_executor" in tool_names

    # Verify the MCP definitions were converted to
    # OpenAI-compatible function tool definitions.
    for tool in tools:
        assert tool["type"] == "function"
        assert "name" in tool["function"]
        assert "description" in tool["function"]
        assert "parameters" in tool["function"]


@pytest.mark.asyncio
async def test_call_sql_tool():
    client = MCPToolClient()

    result = await client.call_tool(
        name="sql_executor",
        arguments={
            "query": """
                SELECT COUNT(*) AS customer_count
                FROM customers;
            """
        },
    )

    assert result

    data = json.loads(result)

    assert "rows" in data
    assert "row_count" in data

    assert data["row_count"] == 1
    assert data["rows"][0]["customer_count"] == 100
