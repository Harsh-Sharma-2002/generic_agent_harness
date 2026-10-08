"""Runtime tracer for KernelAI observability."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from observability.event import RuntimeEvent
from observability.sinks.base import TraceSink


class RuntimeTracer:
    """
    Central event tracer for one KernelAI runtime.

    Runtime components emit facts through this tracer. The tracer
    creates RuntimeEvents and forwards them to every configured
    sink.

    Observability failures must not affect KernelAI execution.
    """

    def __init__(
        self,
        sinks: list[TraceSink],
    ) -> None:
        self.sinks = sinks

    def emit(
        self,
        component: str,
        event: str,
        request_id: str | None = None,
        task_id: str | None = None,
        **data: Any,
    ) -> None:
        """
        Create and emit one RuntimeEvent.
        """

        runtime_event = RuntimeEvent(
            timestamp=datetime.now(
                timezone.utc
            ),
            component=component,
            event=event,
            request_id=request_id,
            task_id=task_id,
            data=data,
        )

        for sink in self.sinks:
            try:
                sink.emit(
                    runtime_event
                )
            except Exception:
                continue