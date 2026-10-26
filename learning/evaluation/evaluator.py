"""Evaluation runner for one KernelAI execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from learning.evaluation.batch_judge import SemanticJudge
from learning.evaluation.checkpoint import (
    EvaluationCheckpoint,
)
from learning.evaluation.deterministic_calculator import (
    calculate_deterministic_execution_metrics,
)
from learning.evaluation.execution import ExecutionRecord
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

    async def evaluate_many(
        self,
        executions: list[ExecutionRecord],
        *,
        policy: str | None = None,
        batch_size: int = 10,
        checkpoint_id: str | None = None,
    ) -> list[ExecutionEvaluation]:
        """
        Evaluate multiple executions using batched semantic scoring.

        Deterministic metrics remain per-execution.

        Normal semantic metrics are evaluated in batches.

        If checkpoint_id is supplied, successfully completed semantic
        batches are temporarily stored and can be restored by a later
        retry using the same checkpoint_id.

        Checkpoint cleanup belongs to the tool layer after permanent
        evaluation persistence succeeds.

        Policy compliance remains targeted/separate.
        """

        if batch_size < 1:
            raise ValueError(
                "batch_size must be at least 1."
            )

        results: dict[
            str,
            ExecutionEvaluation,
        ] = {}

        semantic_eligible: list[
            ExecutionRecord
        ] = []

        # --------------------------------------------------------
        # Deterministic metrics
        # --------------------------------------------------------

        for execution in executions:
            deterministic = (
                calculate_deterministic_execution_metrics(
                    execution
                )
            )

            results[
                execution.request_id
            ] = ExecutionEvaluation(
                request_id=execution.request_id,
                metrics=tuple(
                    deterministic
                ),
            )

            # Same eligibility rule as evaluate().
            if (
                execution.query.strip()
                and execution.final_answer is not None
            ):
                semantic_eligible.append(
                    execution
                )

        # --------------------------------------------------------
        # Build stable semantic batches
        # --------------------------------------------------------

        batches = [
            semantic_eligible[
                start:start + batch_size
            ]
            for start in range(
                0,
                len(semantic_eligible),
                batch_size,
            )
        ]

        total_batches = len(
            batches
        )

        # --------------------------------------------------------
        # Optional temporary checkpoint
        # --------------------------------------------------------

        checkpoint = (
            EvaluationCheckpoint.open(
                checkpoint_id=checkpoint_id,
                executions=semantic_eligible,
                batch_size=batch_size,
            )
            if checkpoint_id is not None
            else None
        )

        print()
        print(
            "[SEMANTIC EVALUATION] "
            f"{len(semantic_eligible)} executions "
            f"across {total_batches} batches"
        )

        if (
            checkpoint is not None
            and checkpoint.completed_count
        ):
            print(
                "[SEMANTIC EVALUATION] "
                f"Checkpoint {checkpoint_id}: "
                f"{checkpoint.completed_count}/"
                f"{total_batches} batches "
                "already complete"
            )

        # --------------------------------------------------------
        # Batched semantic metrics
        # --------------------------------------------------------

        for batch_number, batch in enumerate(
            batches,
            start=1,
        ):
            request_ids = [
                execution.request_id
                for execution in batch
            ]

            # ----------------------------------------------------
            # Restore already-completed batch
            # ----------------------------------------------------

            if (
                checkpoint is not None
                and checkpoint.has_batch(
                    batch_number
                )
            ):
                semantic_results = (
                    checkpoint.load_batch(
                        batch_number=batch_number,
                        executions=batch,
                    )
                )

                print(
                    "[SEMANTIC BATCH] "
                    f"{batch_number}/"
                    f"{total_batches} "
                    "restored from checkpoint"
                )

            # ----------------------------------------------------
            # Otherwise call the semantic judge
            # ----------------------------------------------------

            else:
                print()
                print(
                    "[SEMANTIC BATCH] "
                    f"{batch_number}/"
                    f"{total_batches} "
                    f"starting "
                    f"({len(batch)} executions)"
                )

                try:
                    semantic_results = (
                        await self.judge.score_batch(
                            batch
                        )
                    )

                except Exception as exc:
                    completed_batches = (
                        checkpoint.completed_count
                        if checkpoint is not None
                        else (
                            batch_number - 1
                        )
                    )

                    print()
                    print(
                        "[SEMANTIC BATCH] "
                        f"{batch_number}/"
                        f"{total_batches} "
                        "FAILED"
                    )

                    print(
                        "[SEMANTIC EVALUATION] "
                        f"Completed batches: "
                        f"{completed_batches}/"
                        f"{total_batches}"
                    )

                    print(
                        "[SEMANTIC BATCH] "
                        "Request IDs:"
                    )

                    for request_id in request_ids:
                        print(
                            f"  - {request_id}"
                        )

                    if checkpoint_id is not None:
                        print(
                            "[SEMANTIC EVALUATION] "
                            "Resume with "
                            f"checkpoint_id="
                            f"{checkpoint_id}"
                        )

                    raise RuntimeError(
                        "Semantic batch "
                        f"{batch_number}/"
                        f"{total_batches} failed "
                        f"after "
                        f"{completed_batches} "
                        "completed batches. "
                        f"request_ids="
                        f"{request_ids}"
                    ) from exc

                # ----------------------------------------------
                # Save successful semantic batch
                # ----------------------------------------------

                if checkpoint is not None:
                    checkpoint.save_batch(
                        batch_number=batch_number,
                        executions=batch,
                        semantic_results=(
                            semantic_results
                        ),
                    )

                print(
                    "[SEMANTIC BATCH] "
                    f"{batch_number}/"
                    f"{total_batches} "
                    "complete"
                )

            # ----------------------------------------------------
            # Merge semantic metrics
            # ----------------------------------------------------

            for execution in batch:
                request_id = (
                    execution.request_id
                )

                if (
                    request_id
                    not in semantic_results
                ):
                    raise ValueError(
                        "Semantic batch result "
                        "is missing request "
                        f"{request_id!r}."
                    )

                existing = results[
                    request_id
                ]

                metrics = list(
                    existing.metrics
                )

                metrics.extend(
                    semantic_results[
                        request_id
                    ]
                )

                # Policy compliance remains separate.
                if policy is not None:
                    policy_metrics = (
                        await self.judge.score_policy_compliance(
                            execution,
                            policy=policy,
                        )
                    )

                    metrics.extend(
                        policy_metrics
                    )

                results[
                    request_id
                ] = ExecutionEvaluation(
                    request_id=request_id,
                    metrics=tuple(
                        metrics
                    ),
                )

        print()
        print(
            "[SEMANTIC EVALUATION] "
            "All semantic batches completed."
        )

        # Preserve original execution ordering.
        return [
            results[
                execution.request_id
            ]
            for execution in executions
        ]