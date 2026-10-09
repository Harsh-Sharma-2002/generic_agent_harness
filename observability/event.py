"""Runtime event model for KernelAI observability."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(
    slots=True,
    frozen=True,
)
class RuntimeEvent:
    """
    One observable event emitted by the KernelAI runtime.

    Events describe what happened without influencing runtime
    execution or policy.
    """

    timestamp: datetime

    component: str
    event: str

    request_id: str | None = None
    task_id: str | None = None

    data: dict[str, Any] = field(
        default_factory=dict
    )