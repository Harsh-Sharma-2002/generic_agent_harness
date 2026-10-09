"""JSONL sink for KernelAI observability."""

from __future__ import annotations

import json
from pathlib import Path

from observability.event import RuntimeEvent


class JSONLSink:
    """
    Persist RuntimeEvents as newline-delimited JSON.

    Each event is written immediately as one JSON object so the
    trace remains available even if execution stops unexpectedly.
    """

    def __init__(
        self,
        path: str | Path,
    ) -> None:
        self.path = Path(path)

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    def emit(
        self,
        event: RuntimeEvent,
    ) -> None:
        record = {
            "timestamp": event.timestamp.isoformat(),
            "component": event.component,
            "event": event.event,
            "request_id": event.request_id,
            "task_id": event.task_id,
            **event.data,
        }

        with self.path.open(
            "a",
            encoding="utf-8",
        ) as file:
            file.write(
                json.dumps(
                    record,
                    default=str,
                    ensure_ascii=False,
                )
            )

            file.write("\n")