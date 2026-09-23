"""Web search tool for the KernelAI MCP server."""

from unittest import result
from ddgs import DDGS

def web_search(query:str,max_results: int = 5):
    """
    Search the web and return relevant results.

    Args:
        query: Search query.
        max_results: Maximum number of results to return.

    Returns:
        Search results containing title, URL, and snippet.
    """
    results = DDGS().search.text(
        query= query,
        max_results= max_results
    )

    return[{"title": result.get("title",""),
            "url": result.get("url",""),
            "snipper": result.get("body","")
            } for result in results
    ]