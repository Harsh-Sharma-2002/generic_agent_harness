"""Batch aggregation for KernelAI offline evaluations."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, median

from learning.evaluation.evaluator import ExecutionEvaluation
from learning.evaluation.metrics import (
    MetricName,
    MetricResult,
)


@dataclass(frozen=True)
class MetricAggregate:
    """
    Aggregate statistics for one metric across a batch.
    """

    name: MetricName

    sample_count: int

    mean: float
    median: float

    minimum: float
    maximum: float


@dataclass(frozen=True)
class BatchEvaluation:
    """
    Aggregated evaluation results for one execution batch.
    """

    execution_count: int

    metrics: tuple[
        MetricAggregate,
        ...
    ]


def aggregate_evaluations(
    evaluations: list[ExecutionEvaluation],
) -> BatchEvaluation:
    """
    Aggregate execution-level evaluation results into batch-level
    metric statistics.

    Missing metrics are not treated as zero.
    """

    if not evaluations:
        raise ValueError(
            "Cannot aggregate an empty evaluation batch."
        )

    values: dict[
        MetricName,
        list[float],
    ] = {}

    for evaluation in evaluations:
        _collect_evaluation_metrics(
            evaluation,
            values,
        )

    aggregates = tuple(
        _aggregate_metric(
            name=name,
            values=metric_values,
        )
        for name, metric_values
        in values.items()
    )

    return BatchEvaluation(
        execution_count=len(
            evaluations
        ),
        metrics=aggregates,
    )


def _collect_evaluation_metrics(
    evaluation: ExecutionEvaluation,
    values: dict[MetricName, list[float]],
) -> None:
    """
    Collect numeric metric values from one execution.

    A metric may appear at most once in an ExecutionEvaluation.
    """

    seen: set[MetricName] = set()

    for metric in evaluation.metrics:
        if metric.name in seen:
            raise ValueError(
                "Execution evaluation contains duplicate metric "
                f"{metric.name.value!r}."
            )

        seen.add(
            metric.name
        )

        value = _numeric_value(
            metric
        )

        values.setdefault(
            metric.name,
            [],
        ).append(
            value
        )


def _aggregate_metric(
    *,
    name: MetricName,
    values: list[float],
) -> MetricAggregate:
    """Aggregate one metric across the batch."""

    if not values:
        raise ValueError(
            f"Metric {name.value!r} has no values."
        )

    return MetricAggregate(
        name=name,
        sample_count=len(
            values
        ),
        mean=mean(
            values
        ),
        median=median(
            values
        ),
        minimum=min(
            values
        ),
        maximum=max(
            values
        ),
    )


def _numeric_value(
    metric: MetricResult,
) -> float:
    """
    Convert a MetricResult into its numeric aggregation value.

    Booleans become 1.0 / 0.0, allowing metrics such as COMPLETION
    to aggregate naturally into rates.
    """

    value = metric.value

    if isinstance(
        value,
        bool,
    ):
        return (
            1.0
            if value
            else 0.0
        )

    if isinstance(
        value,
        (int, float),
    ):
        return float(
            value
        )

    raise ValueError(
        f"Metric {metric.name.value!r} "
        "is not numerically aggregatable."
    )