"""KernelAI MCP server."""

from mcp.server import MCPServer


mcp = MCPServer("KernelAI Tools")


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8001,
    )