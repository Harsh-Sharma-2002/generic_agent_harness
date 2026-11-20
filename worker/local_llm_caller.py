"""Local Ollama LLM caller used for development."""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI, BadRequestError
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
        response_format: dict[str, Any] | None = None,
    ) -> ChatCompletionMessage:
        """
        Call the local OpenAI-compatible endpoint.

        The signature is homogeneous with ``LLMCaller.call``. When the
        endpoint rejects the requested schema mode, one bounded
        compatibility retry is made without it: the finalization
        prompt still carries the schema, so the call remains
        structured end to end.
        """

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "reasoning_effort": "none",
        }

        if tools:
            kwargs["tools"] = tools

        if response_format is not None:
            kwargs["response_format"] = response_format

        try:
            response = await self.client.chat.completions.create(
                **kwargs
            )

        except BadRequestError as exc:
            if (
                response_format is None
                or not self._schema_mode_unsupported(exc)
            ):
                raise

            kwargs.pop("response_format", None)

            response = await self.client.chat.completions.create(
                **kwargs
            )

        return response.choices[0].message

    @staticmethod
    def _schema_mode_unsupported(
        exc: BadRequestError,
    ) -> bool:
        """
        Report whether a 400 is specifically about schema mode.

        Only provider rejection of the response format qualifies for
        the fallback; unrelated bad requests still fail.
        """

        message = str(exc).lower()

        return any(
            token in message
            for token in (
                "response_format",
                "json_schema",
                "json schema",
            )
        )
