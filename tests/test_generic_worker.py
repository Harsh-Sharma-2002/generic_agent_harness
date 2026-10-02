"""End-to-end GenericWorker test using the ASU LLM backend."""

from pathlib import Path

import pytest

from worker.generic_worker import GenericWorker
from worker.mcp_client import MCPToolClient


@pytest.mark.asyncio
async def test_generic_worker_text2sql():
    # For Stage 0, we manually perform the role that the
    # orchestrator will eventually handle: skill selection.
    skill = Path(
        "skills/text2sql.md"
    ).read_text(encoding="utf-8")

    worker = GenericWorker(
        tools=MCPToolClient(),
        system_prompt=skill,

        # The future orchestrator will also decide which capabilities
        # this worker instance is allowed to access.
        allowed_tools={"sql_executor"},

        max_iterations=10,
        verbose=True,

        # Default is False, but keeping this explicit makes it obvious
        # that this test must use the ASU LLMCaller, not Ollama.
        local=False,
    )

    result = await worker.run(
        query="How many customers are currently in the database?",
        request_id="asu-text2sql-test-001",
    )

    assert result
    assert result["content"]

    print("\n" + "=" * 60)
    print("FINAL ASU WORKER RESULT")
    print("=" * 60)
    print(result["content"])

@pytest.mark.asyncio
async def test_generic_worker_web_search():
    skill = Path(
        "skills/web_search.md"
    ).read_text(encoding="utf-8")

    worker = GenericWorker(
        tools=MCPToolClient(),
        system_prompt=skill,
        allowed_tools={"web_search"},
        max_iterations=10,
        verbose=True,
        local=False,
    )

    result = await worker.run(
        query=(
            "Search the web for the latest information about "
            "NVIDIA's Blackwell GPUs and give me a short summary."
        ),
        request_id="asu-web-search-test-001",
    )

    assert result
    assert result["content"]

    print("\n" + "=" * 60)
    print("FINAL WEB SEARCH WORKER RESULT")
    print("=" * 60)
    print(result["content"])