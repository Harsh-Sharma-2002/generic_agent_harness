"""Local Ollama LLM caller used for development."""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionMessage,
    ChatCompletionMessageParam,
)


class LocalLLMCaller:
    """OpenAI-compatible caller for a local Ollama model."""

    def __init__(
        self,
        model: str = "qwen3.5:4b",
    ) -> None:
        self.model = model

        self.client = AsyncOpenAI(
            base_url="http://localhost:11434/v1",
            api_key="ollama",
        )

    async def call(
        self,
        messages: list[ChatCompletionMessageParam],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
    ) -> ChatCompletionMessage:

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "reasoning_effort": "none",
        }

        if tools:
            kwargs["tools"] = tools

        response = await self.client.chat.completions.create(**kwargs)

        return response.choices[0].message
