"""Deterministic metric calculator for KernelAI offline evaluation."""

from __future__ import annotations

import json
from learning.evaluation.execution import (
    ExecutionRecord,
    TaskExecutionRecord,
    ToolCallRecord,
)
from learning.evaluation.metrics import (
    MetricName,
    MetricResult,
    MetricSource,
)


def calculate_deterministic_execution_metrics(
    execution: ExecutionRecord,
) -> tuple[MetricResult, ...]:
    """
    Calculate deterministic metrics for one complete execution.

    Metrics that cannot be calculated from the available trace
    data are omitted rather than being reported as fake zeros.
    """

    metrics: list[MetricResult] = []

    metrics.append(
        MetricResult(
            name=MetricName.COMPLETION,
            source=MetricSource.DETERMINISTIC,
            value=(
                execution.status == "completed"
            ),
        )
    )

    metrics.append(
        MetricResult(
            name=MetricName.TOOL_CALLS,
            source=MetricSource.DETERMINISTIC,
            value=sum(
                len(task.tool_calls)
                for task in execution.tasks
            ),
        )
    )

    metrics.append(
        MetricResult(
            name=MetricName.LLM_CALLS,
            source=MetricSource.DETERMINISTIC,
            value=(
                execution.supervisor_llm_calls
                + sum(
                    task.llm_calls
                    for task in execution.tasks
                )
            ),
        )
    )

    if execution.duration_seconds is not None:
        metrics.append(
            MetricResult(
                name=MetricName.LATENCY_SECONDS,
                source=MetricSource.DETERMINISTIC,
                value=execution.duration_seconds,
            )
        )

    if execution.input_tokens is not None:
        metrics.append(
            MetricResult(
                name=MetricName.INPUT_TOKENS,
                source=MetricSource.DETERMINISTIC,
                value=execution.input_tokens,
            )
        )

    if execution.output_tokens is not None:
        metrics.append(
            MetricResult(
                name=MetricName.OUTPUT_TOKENS,
                source=MetricSource.DETERMINISTIC,
                value=execution.output_tokens,
            )
        )

    exact_duplicates = 0
    unchanged_retries = 0

    for task in execution.tasks:
        task_metrics = (
            calculate_deterministic_task_metrics(
                task
            )
        )

        task_metric_values = {
            metric.name: metric.value
            for metric in task_metrics
        }

        exact_duplicates += task_metric_values[
            MetricName.EXACT_DUPLICATE_CALLS
        ]

        unchanged_retries += task_metric_values[
            MetricName.UNCHANGED_RETRIES
        ]

    metrics.append(
        MetricResult(
            name=MetricName.EXACT_DUPLICATE_CALLS,
            source=MetricSource.DETERMINISTIC,
            value=exact_duplicates,
        )
    )

    metrics.append(
        MetricResult(
            name=MetricName.UNCHANGED_RETRIES,
            source=MetricSource.DETERMINISTIC,
            value=unchanged_retries,
        )
    )

    return tuple(metrics)


def calculate_deterministic_task_metrics(
    task: TaskExecutionRecord,
) -> tuple[MetricResult, ...]:
    """
    Calculate deterministic metrics for one Task execution.

    Metrics that cannot be calculated from the available trace
    data are omitted rather than being reported as fake zeros.
    """

    metrics: list[MetricResult] = []

    metrics.append(
        MetricResult(
            name=MetricName.COMPLETION,
            source=MetricSource.DETERMINISTIC,
            value=(task.status == "completed"),
        )
    )

    metrics.append(
        MetricResult(
            name=MetricName.TOOL_CALLS,
            source=MetricSource.DETERMINISTIC,
            value=len(task.tool_calls),
        )
    )

    metrics.append(
        MetricResult(
            name=MetricName.LLM_CALLS,
            source=MetricSource.DETERMINISTIC,
            value=task.llm_calls,
        )
    )

    if task.duration_seconds is not None:
        metrics.append(
            MetricResult(
                name=MetricName.LATENCY_SECONDS,
                source=MetricSource.DETERMINISTIC,
                value=task.duration_seconds,
            )
        )

    metrics.append(
        MetricResult(
            name=MetricName.EXACT_DUPLICATE_CALLS,
            source=MetricSource.DETERMINISTIC,
            value=_count_exact_duplicate_calls(
                task.tool_calls
            ),
        )
    )

    metrics.append(
        MetricResult(
            name=MetricName.UNCHANGED_RETRIES,
            source=MetricSource.DETERMINISTIC,
            value=_count_unchanged_retries(
                task.tool_calls
            ),
        )
    )

    return tuple(metrics)


def _normalized_arguments(
    call: ToolCallRecord,
) -> str:
    """
    Deterministically normalize one tool call's arguments.
    """

    return json.dumps(
        call.arguments,
        sort_keys=True,
        default=str,
        ensure_ascii=False,
    )


def _count_exact_duplicate_calls(
    tool_calls: tuple[ToolCallRecord, ...],
) -> int:
    """
    Count repeated calls with identical tool_name and
    normalized arguments.

    The first occurrence of a call signature is not a
    duplicate.
    """

    seen: set[tuple[str, str]] = set()
    duplicates = 0

    for call in tool_calls:
        signature = (
            call.tool_name,
            _normalized_arguments(call),
        )

        if signature in seen:
            duplicates += 1

        else:
            seen.add(signature)

    return duplicates


def _count_unchanged_retries(
    tool_calls: tuple[ToolCallRecord, ...],
) -> int:
    """
    Count failed tool calls immediately followed by another
    call with identical tool_name and normalized arguments.
    """

    retries = 0

    for index in range(
        len(tool_calls) - 1
    ):
        failed = tool_calls[index]

        if failed.success:
            continue

        retry = tool_calls[index + 1]

        if (
            retry.tool_name
            == failed.tool_name
            and _normalized_arguments(
                retry
            )
            == _normalized_arguments(
                failed
            )
        ):
            retries += 1

    return retries

