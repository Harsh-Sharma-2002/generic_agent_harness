from pathlib import Path

import pytest

from worker.generic_worker import GenericWorker
from worker.llm_caller import LLMCaller
from worker.mcp_client import MCPToolClient


@pytest.mark.asyncio
async def test_generic_worker_text2sql():
    skill = Path(
        "worker/skills/text2sql.md"
    ).read_text(encoding="utf-8")

    worker = GenericWorker(
        llm=LLMCaller(),
        tools=MCPToolClient(),
        system_prompt=skill,
        max_iterations=10,
        verbose=True,
    )

    result = await worker.run(
        query=(
            "How many customers are currently "
            "in the database?"
        ),
        request_id="text2sql-test-001",
    )

    assert result
    assert result["content"]

    print("\nFINAL WORKER RESULT:")
    print(result["content"])
