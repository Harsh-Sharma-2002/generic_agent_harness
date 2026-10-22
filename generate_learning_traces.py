"""Generate production traces for KernelAI offline evaluation."""

from __future__ import annotations

import argparse
import asyncio
import random
from typing import Any

import httpx


API_URL = "http://127.0.0.1:8000"

DEFAULT_REQUEST_COUNT = 100
DEFAULT_CONCURRENCY = 5


# Mix requests so the learner observes different execution behavior.
#
# These intentionally exercise both current production skills:
# - web_search
# - text2sql
#
# Add or replace queries here if you want a different workload.

QUERIES = [
    # --------------------------------------------------------
    # Web search
    # --------------------------------------------------------

    "Find the latest major OpenAI announcement and summarize it.",
    "What is the latest major Anthropic announcement?",
    "Find recent news about NVIDIA AI chips.",
    "What are the latest developments in AI agents?",
    "Find the latest major Google DeepMind announcement.",
    "What is the latest news about AMD AI accelerators?",
    "Find recent developments in open-source language models.",
    "What are the latest developments around MCP?",
    "Find recent news about AI coding agents.",
    "What is the latest major Meta AI announcement?",

    # --------------------------------------------------------
    # Text2SQL
    # --------------------------------------------------------

    "How many customers are in the database?",
    "How many products are in the database?",
    "How many orders are in the database?",
    "What is the total revenue in the database?",
    "Which customer has placed the most orders?",
    "Which product has generated the most revenue?",
    "What are the five most expensive products?",
    "Which category contains the most products?",
    "What is the average product price?",
    "Show the five customers with the most orders.",

    # --------------------------------------------------------
    # Slightly more complex requests
    # --------------------------------------------------------

    "Find the latest OpenAI announcement and explain why it matters.",
    "Find the latest Anthropic announcement and summarize the main change.",
    "Find recent NVIDIA AI news and summarize the most important development.",
    "Which five products generated the most revenue?",
    "Which customers generated the most total revenue?",
    "Compare the number of products across categories.",
    "What is the average order value?",
    "Find recent developments in AI agent frameworks and summarize them.",
    "Find the latest news about model context protocol adoption.",
    "Which product categories generated the most revenue?",
]


async def submit_request(
    client: httpx.AsyncClient,
    query: str,
) -> str:
    """Submit one request and return its request ID."""

    response = await client.post(
        f"{API_URL}/requests",
        json={
            "query": query,
        },
    )

    response.raise_for_status()

    data = response.json()

    return str(
        data["request_id"]
    )


async def wait_for_request(
    client: httpx.AsyncClient,
    request_id: str,
) -> dict[str, Any]:
    """Wait until one submitted request reaches a terminal state."""

    while True:
        response = await client.get(
            f"{API_URL}/requests/{request_id}"
        )

        response.raise_for_status()

        data = response.json()

        status = data.get(
            "status"
        )

        if status in {
            "completed",
            "failed",
            "cancelled",
        }:
            return data

        await asyncio.sleep(
            0.5
        )


async def run_one(
    *,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    index: int,
    query: str,
) -> dict[str, Any]:
    """Submit and await one production request."""

    async with semaphore:
        print(
            f"[{index:03d}] submitting: "
            f"{query}"
        )

        try:
            request_id = await submit_request(
                client,
                query,
            )

            result = await wait_for_request(
                client,
                request_id,
            )

        except Exception as exc:
            print(
                f"[{index:03d}] client error: "
                f"{type(exc).__name__}: {exc}"
            )

            return {
                "index": index,
                "status": "client_error",
                "query": query,
                "error": str(exc),
            }

        status = str(
            result.get(
                "status"
            )
        )

        print(
            f"[{index:03d}] "
            f"{status}: {request_id}"
        )

        return {
            "index": index,
            "request_id": request_id,
            "status": status,
            "query": query,
        }


async def generate(
    *,
    count: int,
    concurrency: int,
) -> None:
    """Generate production executions through the real KernelAI API."""

    semaphore = asyncio.Semaphore(
        concurrency
    )

    timeout = httpx.Timeout(
        300.0
    )

    # Deterministic workload ordering between runs.
    rng = random.Random(
        42
    )

    workload = [
        rng.choice(
            QUERIES
        )
        for _ in range(
            count
        )
    ]

    async with httpx.AsyncClient(
        timeout=timeout
    ) as client:
        results = await asyncio.gather(
            *(
                run_one(
                    client=client,
                    semaphore=semaphore,
                    index=index,
                    query=query,
                )
                for index, query
                in enumerate(
                    workload,
                    start=1,
                )
            )
        )

    completed = sum(
        result["status"] == "completed"
        for result in results
    )

    failed = sum(
        result["status"] == "failed"
        for result in results
    )

    cancelled = sum(
        result["status"] == "cancelled"
        for result in results
    )

    client_errors = sum(
        result["status"] == "client_error"
        for result in results
    )

    print()
    print("=" * 60)
    print("TRACE GENERATION COMPLETE")
    print("=" * 60)
    print(f"Requested:     {count}")
    print(f"Completed:     {completed}")
    print(f"Failed:        {failed}")
    print(f"Cancelled:     {cancelled}")
    print(f"Client errors: {client_errors}")
    print()
    print(
        "Production traces should now be available under:"
    )
    print(
        "learning/data/traces/production/"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate KernelAI production traces for "
            "offline evaluation."
        )
    )

    parser.add_argument(
        "--count",
        type=int,
        default=DEFAULT_REQUEST_COUNT,
    )

    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
    )

    args = parser.parse_args()

    if args.count < 1:
        raise ValueError(
            "--count must be at least 1."
        )

    if args.concurrency < 1:
        raise ValueError(
            "--concurrency must be at least 1."
        )

    asyncio.run(
        generate(
            count=args.count,
            concurrency=args.concurrency,
        )
    )


if __name__ == "__main__":
    main()
