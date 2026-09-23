import pytest

from worker.llm_caller import LLMCaller

@pytest.mark.asyncio
async def test_llm_caller_returns_response():
    llm = LLMCaller()
    response = await llm.call(
        messages=[
            {
                "role": "user",
                "content": "Reply with exactly one word: pong",
            }
        ],
        max_tokens=50,
    )

    assert response.content
    assert response.content.strip().lower() == "pong"
	

