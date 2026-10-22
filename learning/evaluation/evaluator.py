"""Evaluation runner for one KernelAI execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from learning.evaluation.deterministic_calculator import (
    calculate_deterministic_execution_metrics,
)
from learning.evaluation.execution import ExecutionRecord
from learning.evaluation.judge import SemanticJudge
from learning.evaluation.metrics import MetricResult


@dataclass(frozen=True)
class ExecutionEvaluation:
    """
    Complete evaluation result for one KernelAI execution.
    """

    request_id: str
    metrics: tuple[MetricResult, ...]


class ExecutionEvaluator:
    """
    Evaluate one KernelAI execution using deterministic and
    semantic metrics.

    This layer coordinates scoring only. It does not aggregate
    batches, compare policy versions, or make learning decisions.
    """

    def __init__(
        self,
        judge: SemanticJudge | None = None,
    ) -> None:
        self.judge = (
            judge
            if judge is not None
            else SemanticJudge()
        )

    async def evaluate(
        self,
        execution: ExecutionRecord,
        *,
        policy: str | None = None,
    ) -> ExecutionEvaluation:
        """
        Evaluate one execution.

        Independent semantic scores are calculated concurrently.

        Policy compliance is included only when the policy that
        produced the execution is supplied.
        """

        deterministic = (
            calculate_deterministic_execution_metrics(
                execution
            )
        )

        # Semantic scoring requires evidence that failed,
        # cancelled, or otherwise incomplete executions
        # legitimately lack. Such executions still receive
        # their deterministic metrics; semantic metrics are
        # simply absent rather than fabricated.

        if (
            not execution.query.strip()
            or execution.final_answer is None
        ):
            return ExecutionEvaluation(
                request_id=execution.request_id,
                metrics=tuple(
                    deterministic
                ),
            )

        semantic_jobs = [
            self.judge.score_task_fulfillment(
                execution
            ),
            self.judge.score_requirement_coverage(
                execution
            ),
            self.judge.score_groundedness(
                execution
            ),
            self.judge.score_scope_overreach(
                execution
            ),
            self.judge.score_sufficiency_stopping(
                execution
            ),
        ]

        if policy is not None:
            semantic_jobs.append(
                self.judge.score_policy_compliance(
                    execution,
                    policy=policy,
                )
            )

        semantic_results = await asyncio.gather(
            *semantic_jobs
        )

        metrics: list[MetricResult] = list(
            deterministic
        )

        for result in semantic_results:
            if isinstance(
                result,
                MetricResult,
            ):
                metrics.append(
                    result
                )

            else:
                metrics.extend(
                    result
                )

        return ExecutionEvaluation(
            request_id=execution.request_id,
            metrics=tuple(
                metrics
            ),
        )