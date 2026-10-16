"""Local evaluation tools available to the KernelAI Learning Supervisor.

These tools provide progressive access to offline evaluation data:

1. evaluate_trace_batch()
   Evaluate many executions and return only aggregate metric trends.

2. get_metric_outliers()
   Inspect a small number of real executions with the highest or
   lowest scores for one metric.

3. get_execution_details()
   Inspect the underlying trajectory of exactly one execution.

The interface is intentionally progressive so the Learning Supervisor
can begin with compact evidence and request detailed context only when
needed.
"""

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
from learning.evaluation.execution import (
    ExecutionRecord,
)
from learning.evaluation.loader import (
    get_trace_directory,
    load_executions,
)
from learning.evaluation.metrics import (
    MetricName,
    MetricResult,
)
from learning.models import TracePlane


# ============================================================
# Storage
# ============================================================


LEARNING_ROOT = Path(__file__).parent

EVALUATION_ROOT = (
    LEARNING_ROOT
    / "data"
    / "evaluations"
)

MAX_OUTLIER_LIMIT = 10


# ============================================================
# Public Learning Supervisor tools
# ============================================================


async def evaluate_trace_batch(
    *,
    plane: str = "production",
    policy: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """
    Evaluate a batch of stored KernelAI executions.

    Use this tool FIRST when analyzing historical agent behavior.

    The tool:
    - loads executions from the selected trace plane,
    - calculates deterministic metrics,
    - calculates semantic evaluation metrics,
    - aggregates the metrics across the batch,
    - persists detailed per-execution scores for later inspection,
    - returns only compact aggregate statistics.

    This tool intentionally does NOT return individual executions or
    full traces. If an aggregate metric looks problematic, use
    get_metric_outliers() to inspect a small number of relevant
    executions.

    Args:
        plane:
            Trace source to evaluate.

            Supported values:
            - "production"
            - "learning"
            - "evaluation"

            Use "production" when analyzing normal KernelAI behavior.

        policy:
            Optional policy text used to evaluate policy compliance.

            When omitted, policy-compliance metrics are not calculated.

        limit:
            Optional maximum number of most recently loaded executions
            to evaluate.

            When omitted, all available executions in the selected
            trace plane are evaluated.

    Returns:
        A compact batch evaluation containing:

        - evaluation_id:
            Identifier for this evaluation batch. Use this identifier
            with get_metric_outliers().

        - plane:
            Trace plane that was evaluated.

        - execution_count:
            Number of executions evaluated.

        - aggregate_metrics:
            Batch-level statistics such as mean, median, minimum,
            maximum, and sample count for each available metric.

        Detailed per-execution evaluations are persisted locally but
        are intentionally excluded from this response to protect the
        Learning Supervisor's context window.
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

    executions = _load_plane_executions(
        trace_plane
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

    evaluation_list = list(
        evaluations
    )

    batch = aggregate_evaluations(
        evaluation_list
    )

    evaluation_id = (
        _new_evaluation_id()
    )

    _persist_evaluation(
        evaluation_id=evaluation_id,
        plane=trace_plane,
        evaluations=evaluation_list,
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
    }


def get_metric_outliers(
    *,
    evaluation_id: str,
    metric: str,
    direction: str = "lowest",
    limit: int = 5,
) -> dict[str, Any]:
    """
    Return a small number of executions with extreme scores for one
    metric from an existing evaluation batch.

    Use this tool AFTER evaluate_trace_batch() when an aggregate metric
    needs investigation.

    This tool does NOT run another evaluation. It selects already-scored
    executions from the specified evaluation batch.

    Typical use:

        1. evaluate_trace_batch() reports that the average
           sufficiency_stopping score is low.

        2. Call get_metric_outliers(
               evaluation_id=...,
               metric="sufficiency_stopping",
               direction="lowest",
               limit=3,
           )

        3. Inspect the returned judge reasoning and request IDs.

        4. If more evidence is needed for one request, call
           get_execution_details() with that request_id.

    Args:
        evaluation_id:
            Evaluation batch identifier returned by
            evaluate_trace_batch().

        metric:
            Metric to inspect, for example:
            - "task_fulfillment"
            - "requirement_coverage"
            - "policy_compliance"
            - "supported_claim_rate"
            - "scope_overreach"
            - "sufficiency_stopping"
            - "tool_calls"
            - "llm_calls"
            - "latency_seconds"

        direction:
            Which extreme to return.

            - "lowest":
                executions with the smallest metric values.

            - "highest":
                executions with the largest metric values.

            Remember that metric direction differs by metric.
            For example, low task_fulfillment is usually bad while
            high scope_overreach is usually bad.

        limit:
            Maximum number of executions to return.

            Must be between 1 and 10. Prefer small values such as
            3 or 5 to preserve context.

    Returns:
        A bounded collection containing:

        - request_id
        - metric value
        - metric metadata

        Metric metadata may contain judge reasoning or structured
        classifications.

        Full execution trajectories are intentionally excluded.
        Use get_execution_details() when the underlying execution
        must be inspected.
    """

    _validate_evaluation_id(
        evaluation_id
    )

    if (
        limit < 1
        or limit > MAX_OUTLIER_LIMIT
    ):
        raise ValueError(
            "limit must be between 1 and "
            f"{MAX_OUTLIER_LIMIT}."
        )

    if direction not in {
        "lowest",
        "highest",
    }:
        raise ValueError(
            "direction must be 'lowest' or 'highest'."
        )

    metric_name = _parse_metric(
        metric
    )

    evaluation = _load_evaluation(
        evaluation_id
    )

    stored_executions = (
        evaluation.get(
            "executions"
        )
    )

    if not isinstance(
        stored_executions,
        list,
    ):
        raise ValueError(
            "Stored evaluation does not contain "
            "a valid executions list."
        )

    outliers: list[
        dict[str, Any]
    ] = []

    for execution in stored_executions:
        if not isinstance(
            execution,
            dict,
        ):
            continue

        metrics = execution.get(
            "metrics"
        )

        if not isinstance(
            metrics,
            list,
        ):
            continue

        for result in metrics:
            if not isinstance(
                result,
                dict,
            ):
                continue

            if (
                result.get("name")
                != metric_name.value
            ):
                continue

            value = result.get(
                "value"
            )

            if not _is_numeric(
                value
            ):
                continue

            outliers.append(
                {
                    "request_id": (
                        execution.get(
                            "request_id"
                        )
                    ),
                    "value": value,
                    "metadata": (
                        result.get(
                            "metadata"
                        )
                    ),
                }
            )

            break

    outliers.sort(
        key=lambda item: float(
            item["value"]
        ),
        reverse=(
            direction == "highest"
        ),
    )

    selected = outliers[
        :limit
    ]

    return {
        "evaluation_id": evaluation_id,
        "metric": metric_name.value,
        "direction": direction,
        "available": len(
            outliers
        ),
        "returned": len(
            selected
        ),
        "outliers": selected,
    }


def get_execution_details(
    *,
    request_id: str,
    plane: str = "production",
) -> dict[str, Any]:
    """
    Return the underlying execution trajectory for exactly one
    KernelAI request.

    Use this tool only when aggregate metrics and metric outliers are
    insufficient to explain why an execution behaved poorly or well.

    This is the deepest evaluation drill-down exposed to the Learning
    Supervisor and may return substantially more context than the other
    evaluation tools.

    Recommended investigation order:

        evaluate_trace_batch()
            ->
        get_metric_outliers()
            ->
        get_execution_details()

    Args:
        request_id:
            Request identifier obtained from get_metric_outliers()
            or another trusted evaluation result.

        plane:
            Trace plane containing the execution.

            Supported values:
            - "production"
            - "learning"
            - "evaluation"

            Usually this should match the plane used when the evaluation
            batch was created.

    Returns:
        One structured execution containing:

        - original user query,
        - final answer,
        - execution status and duration,
        - task decomposition,
        - skills assigned to each task,
        - LLM-call counts,
        - tool names,
        - tool arguments,
        - bounded tool-result previews,
        - tool success/failure information.

        Exactly one execution is returned. This tool never returns an
        entire batch.
    """

    if not request_id.strip():
        raise ValueError(
            "request_id must not be empty."
        )

    trace_plane = _parse_plane(
        plane
    )

    executions = _load_plane_executions(
        trace_plane
    )

    for execution in executions:
        if (
            execution.request_id
            == request_id
        ):
            return _serialize_execution(
                execution
            )

    raise FileNotFoundError(
        f"Execution not found for request "
        f"{request_id!r} in plane "
        f"{trace_plane.value!r}."
    )


# ============================================================
# Execution loading
# ============================================================


def _load_plane_executions(
    plane: TracePlane,
) -> list[ExecutionRecord]:
    """
    Load every Request execution stored for one trace plane.

    This helper is internal. Learning Supervisor tools should use the
    public progressive-disclosure functions above rather than loading
    complete trace planes directly.
    """

    directory = get_trace_directory(
        plane
    )

    executions: list[
        ExecutionRecord
    ] = []

    for path in _trace_files(
        directory
    ):
        executions.extend(
            load_executions(
                path
            )
        )

    return executions


def _trace_files(
    directory: Path,
) -> list[Path]:
    """
    Return JSONL trace files from one trace directory in deterministic
    filename order.
    """

    if not directory.exists():
        return []

    if not directory.is_dir():
        raise ValueError(
            f"Trace path is not a directory: "
            f"{directory}"
        )

    return sorted(
        path
        for path in directory.glob(
            "*.jsonl"
        )
        if path.is_file()
    )


# ============================================================
# Evaluation persistence
# ============================================================


def _persist_evaluation(
    *,
    evaluation_id: str,
    plane: TracePlane,
    evaluations: list[
        ExecutionEvaluation
    ],
) -> Path:
    """
    Persist detailed per-execution evaluation results.

    Detailed results remain outside the Learning Supervisor's context
    until explicitly requested through get_metric_outliers().
    """

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


def _load_evaluation(
    evaluation_id: str,
) -> dict[str, Any]:
    """
    Load one persisted evaluation batch by its opaque identifier.
    """

    _validate_evaluation_id(
        evaluation_id
    )

    path = (
        EVALUATION_ROOT
        / f"{evaluation_id}.json"
    )

    if not path.exists():
        raise FileNotFoundError(
            f"Evaluation not found: "
            f"{evaluation_id}"
        )

    if not path.is_file():
        raise ValueError(
            "Evaluation path is not a file."
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        result = json.load(
            file
        )

    if not isinstance(
        result,
        dict,
    ):
        raise ValueError(
            "Stored evaluation must contain "
            "a JSON object."
        )

    return result


# ============================================================
# Serialization
# ============================================================


def _serialize_evaluation(
    evaluation: ExecutionEvaluation,
) -> dict[str, Any]:
    """
    Serialize one execution's scores for durable evaluation storage.
    """

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
    """
    Serialize one deterministic or semantic metric.
    """

    return {
        "name": metric.name.value,
        "source": metric.source.value,
        "value": metric.value,
        "metadata": metric.metadata,
    }


def _serialize_execution(
    execution: ExecutionRecord,
) -> dict[str, Any]:
    """
    Serialize one execution for deep diagnostic inspection.

    Tool results are already bounded by the observability trace
    contract from which ExecutionRecord was reconstructed.
    """

    return {
        "request_id": (
            execution.request_id
        ),
        "query": (
            execution.query
        ),
        "status": (
            execution.status
        ),
        "final_answer": (
            execution.final_answer
        ),
        "duration_seconds": (
            execution.duration_seconds
        ),
        "supervisor_llm_calls": (
            execution.supervisor_llm_calls
        ),
        "input_tokens": (
            execution.input_tokens
        ),
        "output_tokens": (
            execution.output_tokens
        ),
        "tasks": [
            {
                "task_id": (
                    task.task_id
                ),
                "description": (
                    task.description
                ),
                "skills": list(
                    task.skills
                ),
                "status": (
                    task.status
                ),
                "llm_calls": (
                    task.llm_calls
                ),
                "duration_seconds": (
                    task.duration_seconds
                ),
                "tool_calls": [
                    {
                        "tool_call_id": (
                            call.tool_call_id
                        ),
                        "tool_name": (
                            call.tool_name
                        ),
                        "arguments": (
                            call.arguments
                        ),
                        "result": (
                            call.result
                        ),
                        "success": (
                            call.success
                        ),
                        "duration_seconds": (
                            call.duration_seconds
                        ),
                        "iteration": (
                            call.iteration
                        ),
                        "error": (
                            call.error
                        ),
                    }
                    for call in task.tool_calls
                ],
            }
            for task in execution.tasks
        ],
    }


# ============================================================
# Argument parsing and validation
# ============================================================


def _parse_plane(
    value: str,
) -> TracePlane:
    """
    Convert a public string argument into a validated TracePlane.
    """

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


def _parse_metric(
    value: str,
) -> MetricName:
    """
    Convert a public metric string into a validated MetricName.
    """

    try:
        return MetricName(
            value
        )

    except ValueError as exc:
        valid = ", ".join(
            metric.value
            for metric in MetricName
        )

        raise ValueError(
            f"Unknown metric {value!r}. "
            f"Expected one of: {valid}."
        ) from exc


def _validate_evaluation_id(
    evaluation_id: str,
) -> None:
    """
    Validate an evaluation identifier before local file lookup.

    Evaluation IDs are opaque identifiers, not filesystem paths.
    """

    if not evaluation_id.strip():
        raise ValueError(
            "evaluation_id must not be empty."
        )

    if (
        "/" in evaluation_id
        or "\\" in evaluation_id
        or ".." in evaluation_id
    ):
        raise ValueError(
            "Invalid evaluation_id."
        )


def _is_numeric(
    value: Any,
) -> bool:
    """
    Return whether a stored metric can be ordered numerically.
    """

    return isinstance(
        value,
        (int, float, bool),
    )


# ============================================================
# Evaluation identifiers
# ============================================================


def _new_evaluation_id() -> str:
    """
    Generate a unique, time-sortable identifier for one evaluation
    batch.
    """

    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )

    return (
        f"eval_{timestamp}"
    )