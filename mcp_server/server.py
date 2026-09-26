"""KernelAI MCP server."""

from mcp.server import MCPServer
from mcp_server.tools.web_search import web_search
from mcp_server.tools.sql_executor import sql_executor


mcp = MCPServer("KernelAI Tools")

mcp.tool()(web_search)
mcp.tool()(sql_executor)

if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8001,
    )