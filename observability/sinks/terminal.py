"""Terminal sink for KernelAI observability."""

from __future__ import annotations

from typing import Any

from observability.event import RuntimeEvent


class TerminalSink:
    """
    Render RuntimeEvents as compact human-readable terminal output.

    Events remain interleaved so concurrent KernelAI activity
    can be observed in execution order.
    """

    def emit(
        self,
        event: RuntimeEvent,
    ) -> None:
        timestamp = (
            event.timestamp
            .strftime("%H:%M:%S.%f")[:-3]
        )

        request_id = (
            event.request_id
            if event.request_id is not None
            else "-"
        )

        task_id = (
            event.task_id
            if event.task_id is not None
            else "-"
        )

        details = self._format_data(
            event.data
        )

        line = (
            f"{timestamp:<12}  "
            f"{event.component.upper():<16}  "
            f"{request_id:<18}  "
            f"{task_id:<38}  "
            f"{event.event.upper():<26}"
        )

        if details:
            line += f"  {details}"

        print(
            line,
            flush=True,
        )

    def _format_data(
        self,
        data: dict[str, Any],
    ) -> str:
        """
        Format event metadata for compact terminal display.
        """

        if not data:
            return ""

        parts: list[str] = []

        for key, value in data.items():
            if key == "error_message":
                parts.append(
                    f'{key}="{value}"'
                )
            else:
                parts.append(
                    f"{key}={value}"
                )

        return " ".join(
            parts
        )