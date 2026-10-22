"""Local tools available to the KernelAI Learning Supervisor.

These tools support one offline learning cycle:

1. evaluate_trace_batch()
   Measure aggregate behavior across historical executions.

2. get_metric_outliers()
   Inspect a small number of executions responsible for unusually
   high or low metric values.

3. get_execution_details()
   Inspect one underlying execution when deeper diagnosis is needed.

4. list_learning_policies()
   Discover which policies may be learned automatically.

5. get_current_policy()
   Read the current production policy after evidence identifies it
   as a likely source of recurring behavior.

6. propose_policy_candidate()
   Snapshot the current production policy and store an isolated
   candidate replacement.

The Learning Supervisor cannot modify production policies through
this module. Candidate creation and production release are separate
capabilities.
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
from learning.models import (
    LearningMode,
    TracePlane,
)
from learning.policies.store import (
    create_policy_candidate,
    list_policies,
    read_current_policy,
    snapshot_current_policy,
)


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
# Evaluation tools
# ============================================================


async def evaluate_trace_batch(
    *,
    plane: str = "production",
    policy: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """
    Evaluate historical KernelAI executions and return aggregate
    metric trends.

    Use this tool FIRST when starting an offline learning analysis.

    The tool loads stored executions, calculates deterministic and
    semantic metrics, aggregates them across the batch, and persists
    detailed per-execution scores for optional later inspection.

    Individual executions are intentionally excluded from the result
    to keep Learning Supervisor context small.

    Args:
        plane:
            Trace source to evaluate.

            Supported values:
            - "production"
            - "learning"
            - "evaluation"

            Normally use "production" when learning from real KernelAI
            behavior.

        policy:
            Optional complete policy text used to calculate policy
            compliance metrics.

            When omitted, policy-compliance metrics are not calculated.

        limit:
            Optional maximum number of recent executions to evaluate.

    Returns:
        Compact batch-level evidence containing:

        - evaluation_id:
            Identifier used with get_metric_outliers().

        - plane:
            Evaluated trace plane.

        - execution_count:
            Number of executions evaluated.

        - aggregate_metrics:
            Mean, median, minimum, maximum, and sample count for every
            available metric.

    This tool only evaluates behavior. It does not modify policies.
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

    evaluations = await evaluator.evaluate_many(
        executions,
        policy=policy,
        batch_size=10,
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
    Return a small number of already-scored executions with extreme
    values for one metric.

    Use this tool AFTER evaluate_trace_batch() when an aggregate metric
    needs investigation.

    This tool does not run another evaluation. It selects executions
    from the previously evaluated batch.

    Args:
        evaluation_id:
            Identifier returned by evaluate_trace_batch().

        metric:
            Metric whose extreme executions should be inspected.

            Examples:
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
            "lowest" returns the smallest metric values.
            "highest" returns the largest metric values.

            Metric direction matters. For example:
            - low task_fulfillment is usually bad,
            - high scope_overreach is usually bad.

        limit:
            Number of executions to return.

            Must be between 1 and 10. Prefer 3-5 to preserve context.

    Returns:
        Bounded scored examples containing:

        - request_id
        - metric value
        - metric metadata / judge reasoning

    Full execution trajectories are intentionally excluded. Call
    get_execution_details() only when one execution requires deeper
    diagnosis.
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

    stored_executions = evaluation.get(
        "executions"
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
    Return the underlying trajectory of exactly one KernelAI request.

    Use this tool only when aggregate metrics and metric outliers are
    insufficient to explain a behavior.

    Recommended investigation order:

        evaluate_trace_batch()
            ->
        get_metric_outliers()
            ->
        get_execution_details()

    Args:
        request_id:
            Request identifier obtained from an evaluation result.

        plane:
            Trace plane containing the request.

    Returns:
        One execution containing its query, final answer, tasks,
        assigned skills, LLM-call counts, and tool-call trajectory.

    This is the deepest evaluation drill-down and may consume
    substantially more context than the other evaluation tools.
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
# Policy discovery
# ============================================================


def list_learning_policies() -> dict[str, Any]:
    """
    List policies known to the learning system and the Learning
    Supervisor's configured authority for each policy.

    Use this tool before proposing a policy candidate.

    Learning modes:

    - "enabled":
        The Learning Supervisor may create an isolated candidate.

    - "manual_only":
        The policy may be inspected and analyzed, but the Learning
        Supervisor may not create a candidate automatically.

    - "disabled":
        Offline learning must not create candidates for this policy.

    Returns:
        Known policy names and their learning modes.

    This tool does not return policy contents and does not modify
    anything.
    """

    config = load_learning_config()

    policies: list[
        dict[str, str]
    ] = []

    for policy_name in list_policies():
        policy_config = config.policies.get(
            policy_name
        )

        mode = (
            policy_config.learning
            if policy_config is not None
            else LearningMode.DISABLED
        )

        policies.append(
            {
                "policy_name": policy_name,
                "learning_mode": (
                    mode.value
                ),
            }
        )

    return {
        "learning_enabled": (
            config.enabled
        ),
        "policies": policies,
    }


def get_current_policy(
    *,
    policy_name: str,
) -> dict[str, Any]:
    """
    Read the current production policy without modifying it.

    Use this tool only after evaluation evidence identifies a policy
    as a plausible source of recurring behavior.

    Args:
        policy_name:
            Known semantic policy identifier.

            Examples:
            - "web_search"
            - "text2sql"
            - "supervisor"
            - "task_worker"

    Returns:
        - policy_name
        - configured learning_mode
        - complete current production policy content

    This operation is read-only.
    """

    config = load_learning_config()

    policy_config = config.policies.get(
        policy_name
    )

    mode = (
        policy_config.learning
        if policy_config is not None
        else LearningMode.DISABLED
    )

    return {
        "policy_name": policy_name,
        "learning_mode": mode.value,
        "content": read_current_policy(
            policy_name
        ),
    }


# ============================================================
# Candidate creation
# ============================================================


def propose_policy_candidate(
    *,
    policy_name: str,
    content: str,
) -> dict[str, Any]:
    """
    Store an isolated candidate replacement for one production policy.

    Use this tool only when batch evaluation and relevant execution
    evidence support a recurring policy-level problem.

    The complete current production policy is snapshotted before the
    candidate is created.

    Candidate creation is permitted only when:

    - offline learning is globally enabled, and
    - the target policy has learning mode "enabled".

    Policies configured as "manual_only" or "disabled" cannot be
    changed through this tool.

    Args:
        policy_name:
            Production policy the candidate is intended to improve.

        content:
            Complete proposed replacement policy.

            Supply the full policy, not a patch or partial fragment.

    Returns:
        - policy_name
        - baseline_version_id
        - candidate_id
        - production_modified=False

    IMPORTANT:
        This tool does NOT overwrite the production policy.
        It does NOT release or activate the candidate.
        Normal KernelAI runtime behavior remains unchanged.
    """

    config = load_learning_config()

    if not config.enabled:
        raise RuntimeError(
            "Offline learning is disabled."
        )

    policy_config = config.policies.get(
        policy_name
    )

    if policy_config is None:
        raise ValueError(
            f"Policy {policy_name!r} has no "
            "learning configuration."
        )

    mode = policy_config.learning

    if mode == LearningMode.DISABLED:
        raise PermissionError(
            f"Learning is disabled for policy "
            f"{policy_name!r}."
        )

    if mode == LearningMode.MANUAL_ONLY:
        raise PermissionError(
            f"Policy {policy_name!r} is manual-only. "
            "Automatic candidate creation is not permitted."
        )

    if mode != LearningMode.ENABLED:
        raise PermissionError(
            f"Candidate creation is not permitted "
            f"for policy {policy_name!r}."
        )

    if not isinstance(
        content,
        str,
    ):
        raise TypeError(
            "Candidate content must be a string."
        )

    if not content.strip():
        raise ValueError(
            "Candidate content must not be empty."
        )

    baseline_version_id = (
        snapshot_current_policy(
            policy_name
        )
    )

    candidate_id = (
        create_policy_candidate(
            policy_name,
            content,
        )
    )

    return {
        "policy_name": policy_name,
        "baseline_version_id": (
            baseline_version_id
        ),
        "candidate_id": (
            candidate_id
        ),
        "production_modified": False,
    }


# ============================================================
# Execution loading
# ============================================================


def _load_plane_executions(
    plane: TracePlane,
) -> list[ExecutionRecord]:
    """
    Load all Request executions stored for one trace plane.
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
    Return JSONL trace files in deterministic filename order.
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
    Persist detailed per-execution evaluation scores.

    Detailed results remain outside Learning Supervisor context until
    explicitly requested through get_metric_outliers().
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
    """Load one persisted evaluation batch."""

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
    """Serialize one execution's evaluation scores."""

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
    """Serialize one deterministic or semantic metric."""

    return {
        "name": metric.name.value,
        "source": metric.source.value,
        "value": metric.value,
        "metadata": metric.metadata,
    }


def _serialize_execution(
    execution: ExecutionRecord,
) -> dict[str, Any]:
    """Serialize one execution for deep diagnostic inspection."""

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
# Validation
# ============================================================


def _parse_plane(
    value: str,
) -> TracePlane:
    """Validate a trace-plane argument."""

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
    """Validate a metric-name argument."""

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
    Validate an opaque evaluation identifier.

    Evaluation IDs are identifiers, not filesystem paths.
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
    """Return whether a metric value can be numerically ordered."""

    return isinstance(
        value,
        (int, float, bool),
    )


# ============================================================
# Evaluation identifiers
# ============================================================


def _new_evaluation_id() -> str:
    """Generate a unique, sortable evaluation-batch identifier."""

    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )

    return (
        f"eval_{timestamp}"
    )