"""Evaluation workload models for KernelAI offline learning.

An evaluation workload is a fixed collection of historical user
requests used to test a candidate policy.

The same workload can be executed against different policy versions,
allowing their resulting metrics to be compared on equivalent inputs.

This module selects and represents workloads. It does not execute
KernelAI, score results, modify policies, or release candidates.
"""

from __future__ import annotations

from dataclasses import dataclass

from learning.evaluation.execution import ExecutionRecord


@dataclass(frozen=True)
class EvaluationCase:
    """
    One historical user request selected for candidate evaluation.

    source_request_id identifies the production execution from which
    the request originated.

    query is the original user request that will be replayed.
    """

    source_request_id: str
    query: str


@dataclass(frozen=True)
class EvaluationWorkload:
    """
    Fixed collection of requests used for one candidate experiment.

    The workload is immutable so baseline and candidate evaluation can
    use exactly the same inputs.
    """

    cases: tuple[EvaluationCase, ...]

    @property
    def size(self) -> int:
        """Return the number of evaluation cases."""

        return len(
            self.cases
        )


def build_workload(
    executions: list[ExecutionRecord],
    *,
    limit: int | None = None,
) -> EvaluationWorkload:
    """
    Build an evaluation workload from historical executions.

    Only executions with a non-empty original query are eligible.

    Args:
        executions:
            Historical executions from which user requests are selected.

        limit:
            Optional maximum number of cases.

            When supplied, the most recent eligible executions from the
            provided sequence are selected.

    Returns:
        Immutable EvaluationWorkload containing the selected requests.

    Raises:
        ValueError:
            If limit is invalid or no eligible executions exist.
    """

    if (
        limit is not None
        and limit < 1
    ):
        raise ValueError(
            "limit must be at least 1."
        )

    eligible = [
        execution
        for execution in executions
        if execution.query.strip()
    ]

    if limit is not None:
        eligible = eligible[
            -limit:
        ]

    if not eligible:
        raise ValueError(
            "Cannot build an evaluation workload "
            "without eligible executions."
        )

    return EvaluationWorkload(
        cases=tuple(
            EvaluationCase(
                source_request_id=(
                    execution.request_id
                ),
                query=execution.query,
            )
            for execution in eligible
        )
    )