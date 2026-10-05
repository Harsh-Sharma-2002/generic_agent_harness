"""KernelAI user request representation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Request:
    """
    A user-level request entering KernelAI.
    """

    request_id: str
    query: str
