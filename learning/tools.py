"""Local privileged tools for the KernelAI learning supervisor."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from learning.config.loader import load_learning_config
from learning.evaluation.aggregator import (
    aggregate_evaluations,
)
from learning.evaluation.evaluator import (
    ExecutionEvaluation,
    ExecutionEvaluator,
)
from learning.evaluation.loader import (
    get_trace_directory,
    load_executions,
)
from learning.evaluation.metrics import MetricResult
from learning.models import TracePlane


EVALUATION_ROOT = (
    Path(__file__).parent
    / "data"
    / "evaluations"
)


async def evaluate_trace_batch(
    *,
    plane: str = "production",
    policy: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """
    Evaluate a stored KernelAI trace batch.

    Full per-execution evaluations are persisted locally.

    Only compact aggregate metrics are returned to the caller so
    large batches do not consume Learning Supervisor context.
    """

    config = load_learning_config()

    if not config.enabled:
        raise RuntimeError(
            "Offline learning is disabled."
        )

    trace_plane = _parse_plane(
        plane
    )

    if (
        limit is not None
        and limit < 1
    ):
        raise ValueError(
            "limit must be at least 1."
        )

    executions = []

    trace_directory = get_trace_directory(
        trace_plane
    )

    for path in _trace_files(
        trace_directory
    ):
        executions.extend(
            load_executions(
                path
            )
        )

    if limit is not None:
        executions = executions[
            -limit:
        ]

    if len(executions) < config.minimum_samples:
        raise ValueError(
            "Insufficient executions for batch evaluation: "
            f"found {len(executions)}, "
            f"minimum is {config.minimum_samples}."
        )

    evaluator = ExecutionEvaluator()

    evaluations = await asyncio.gather(
        *(
            evaluator.evaluate(
                execution,
                policy=policy,
            )
            for execution in executions
        )
    )

    batch = aggregate_evaluations(
        list(evaluations)
    )

    evaluation_id = _new_evaluation_id()

    output_path = _persist_evaluation(
        evaluation_id=evaluation_id,
        plane=trace_plane,
        evaluations=list(
            evaluations
        ),
    )

    return {
        "evaluation_id": evaluation_id,
        "plane": trace_plane.value,
        "execution_count": (
            batch.execution_count
        ),
        "aggregate_metrics": [
            asdict(metric)
            for metric in batch.metrics
        ],
        "details_saved": True,
        "details_path": str(
            output_path
        ),
    }


def _trace_files(
    directory: Path,
) -> list[Path]:
    """Return persisted JSONL trace files in deterministic order."""

    if not directory.exists():
        return []

    return sorted(
        path
        for path in directory.glob(
            "*.jsonl"
        )
        if path.is_file()
    )


def _parse_plane(
    value: str,
) -> TracePlane:
    """Validate an execution-plane tool argument."""

    try:
        return TracePlane(
            value
        )

    except ValueError as exc:
        valid = ", ".join(
            plane.value
            for plane in TracePlane
        )

        raise ValueError(
            f"Unknown trace plane {value!r}. "
            f"Expected one of: {valid}."
        ) from exc


def _new_evaluation_id() -> str:
    """Generate a sortable identifier for one evaluation batch."""

    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )

    return (
        f"eval_{timestamp}"
    )


def _persist_evaluation(
    *,
    evaluation_id: str,
    plane: TracePlane,
    evaluations: list[ExecutionEvaluation],
) -> Path:
    """Persist detailed per-execution evaluation results."""

    EVALUATION_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = (
        EVALUATION_ROOT
        / f"{evaluation_id}.json"
    )

    payload = {
        "evaluation_id": evaluation_id,
        "plane": plane.value,
        "execution_count": len(
            evaluations
        ),
        "executions": [
            _serialize_evaluation(
                evaluation
            )
            for evaluation in evaluations
        ],
    }

    with path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    return path


def _serialize_evaluation(
    evaluation: ExecutionEvaluation,
) -> dict[str, Any]:
    """Serialize one detailed execution evaluation."""

    return {
        "request_id": (
            evaluation.request_id
        ),
        "metrics": [
            _serialize_metric(
                metric
            )
            for metric in evaluation.metrics
        ],
    }


def _serialize_metric(
    metric: MetricResult,
) -> dict[str, Any]:
    """Serialize one metric for durable evaluation storage."""

    return {
        "name": metric.name.value,
        "source": metric.source.value,
        "value": metric.value,
        "metadata": metric.metadata,
    }