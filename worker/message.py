"""Structured KernelAI response content."""

from __future__ import annotations

from pydantic import BaseModel


class KernelAIResponseContent(BaseModel):
    """
    Structured content produced by the model on every worker iteration.

    reasoning:
        A short, high-level explanation of the current action or conclusion.

    answer:
        The final user-facing answer. None while the task is still running.
    """

    reasoning: str
    answer: str | None = None