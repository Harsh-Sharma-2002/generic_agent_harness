import pytest

from mcp.client import Client


MCP_URL = "http://localhost:8001/mcp"


@pytest.mark.asyncio
async def test_mcp_tools():
    async with Client(MCP_URL) as client:

        # Verify both tools are advertised.
        tool_result = await client.list_tools()

        tool_names = {
            tool.name
            for tool in tool_result.tools
        }

        assert "web_search" in tool_names
        assert "sql_executor" in tool_names

        # Test web search.
        search_result = await client.call_tool(
            "web_search",
            {
                "query": "NVIDIA",
                "max_results": 3,
            },
        )

        assert not search_result.is_error
        assert search_result.content

        print("\nWeb search result:")
        print(search_result)

        # Test PostgreSQL.
        sql_result = await client.call_tool(
            "sql_executor",
            {
                "query": """
                SELECT COUNT(*) AS customer_count
                FROM customers;
                """
            },
        )

        assert not sql_result.is_error
        assert sql_result.content

        print("\nSQL result:")
        print(sql_result)