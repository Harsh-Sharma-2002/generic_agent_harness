"""Web search tool for the KernelAI MCP server."""

from __future__ import annotations

import asyncio
from typing import Any

from ddgs import DDGS


async def web_search(
    query: str,
    max_results: int = 5,
    timelimit: str | None = None,
) -> dict[str, Any]:
    """
    Search the web for information relevant to the given query.

    Args:
        query:
            Search query.

        max_results:
            Maximum number of results to return.

        timelimit:
            Optional recency restriction.
            Supported values are:
            - "d" for past day,
            - "w" for past week,
            - "m" for past month,
            - "y" for past year,
            - None for no restriction.

    Returns:
        Structured search outcome containing:

        - success:
            Whether the search completed successfully.

        - error:
            Failure description when the search was unsuccessful,
            otherwise None.

        - results:
            Search results containing title, URL, and snippet.

    Search-provider failures are returned as structured results rather
    than raised as tool-level exceptions. This allows the Task Worker
    to decide whether to recover, reformulate the query, or stop.

    Infrastructure-level failures outside this tool remain visible to
    KernelAI through the normal MCP failure path.
    """

    def _search() -> dict[str, Any]:
        try:
            results = list(
                DDGS().text(
                    query,
                    max_results=max_results,
                    timelimit=timelimit,
                )
            )

            search_results = [
                {
                    "title": item.get(
                        "title",
                        "",
                    ),
                    "url": item.get(
                        "href",
                        "",
                    ),
                    "snippet": item.get(
                        "body",
                        "",
                    ),
                }
                for item in results
            ]

            return {
                "success": True,
                "error": None,
                "results": search_results,
            }

        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
                "results": [],
            }

    return await asyncio.to_thread(
        _search
    )