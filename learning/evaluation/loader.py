"""Load persisted KernelAI trace events for offline evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


TRACE_ROOT = (
    Path(__file__).parents[1]
    / "data"
    / "traces"
)


def load_trace_file(
    path: Path,
) -> list[dict[str, Any]]:
    """
    Load one KernelAI JSONL trace file.

    Each non-empty line must contain one serialized RuntimeEvent.
    """

    if not path.exists():
        raise FileNotFoundError(
            f"Trace file not found: {path}"
        )

    if not path.is_file():
        raise ValueError(
            f"Trace path is not a file: {path}"
        )

    events: list[dict[str, Any]] = []

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        for line_number, line in enumerate(
            file,
            start=1,
        ):
            line = line.strip()

            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON in trace file "
                    f"{path} at line {line_number}."
                ) from exc

            if not isinstance(event, dict):
                raise ValueError(
                    f"Trace event at line {line_number} "
                    f"in {path} must be a JSON object."
                )

            events.append(event)

    return events


def get_trace_directory(
    plane: str,
) -> Path:
    """
    Return the trace directory for one execution plane.
    """

    if plane not in {
        "production",
        "learning",
        "evaluation",
    }:
        raise ValueError(
            f"Unknown trace plane: {plane!r}."
        )

    return TRACE_ROOT / plane