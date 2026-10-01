from pathlib import Path

import pytest

from worker.generic_worker import GenericWorker
from worker.mcp_client import MCPToolClient


@pytest.mark.asyncio
async def test_generic_worker_text2sql():
    # For this test we manually perform the role that the
    # orchestrator will eventually handle: selecting a skill.
    skill = Path(
        "worker/skills/text2sql.md"
    ).read_text(encoding="utf-8")

    worker = GenericWorker(
        tools=MCPToolClient(),
        system_prompt=skill,
        allowed_tools={"sql_executor"},
        max_iterations=10,
        verbose=True,
        local=True,
    )

    result = await worker.run(
        query="How many customers are currently in the database?",
        request_id="text2sql-test-001",
    )

    assert result
    assert result["content"]

    # We know the seeded ecommerce database contains 100 customers.
    assert "100" in result["content"]

    print("\n" + "=" * 60)
    print("FINAL WORKER RESULT")
    print("=" * 60)
    print(result["content"])
