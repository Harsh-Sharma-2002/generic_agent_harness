"""Base sink interface for KernelAI observability."""

from __future__ import annotations

from typing import Protocol

from observability.event import RuntimeEvent


class TraceSink(Protocol):
    """
    Interface implemented by observability sinks.

    A sink receives RuntimeEvents and decides how to represent
    or persist them.
    """

    def emit(
        self,
        event: RuntimeEvent,
    ) -> None:
        ...