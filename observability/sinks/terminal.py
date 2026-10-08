"""Terminal sink for KernelAI observability."""

from __future__ import annotations

from observability.event import RuntimeEvent

class TerminalSink:
    """
    Render RuntimeEvents as compact terminal output.

    Events remain interleaved so concurrent KernelAI activity
    can be observed in execution order.
    """

    def emit(
        self,
        event: RuntimeEvent,
    ) -> None:
        timestamp = event.timestamp.strftime("%H:%M:%S.%f")[:-3]

        parts = [
            timestamp,
            event.component.upper(),
        ]

        if event.request_id is not None:
            parts.append(
                event.request_id
            )

        if event.task_id is not None:
            parts.append(
                event.task_id
            )

        parts.append(
            event.event.upper()
        )

        if event.data:
            details = " ".join(
                f"{key}={value}"
                for key, value in event.data.items()
            )

            parts.append(
                details
            )

        print(
            "  ".join(parts),
            flush=True,
        )