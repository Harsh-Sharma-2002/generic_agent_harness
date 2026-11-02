"""Test batched semantic evaluation checkpoint/resume behavior."""

from __future__ import annotations

import asyncio
import uuid

from learning.evaluation.checkpoint import (
    checkpoint_path,
    delete_checkpoint,
)
from learning.evaluation.evaluator import (
    ExecutionEvaluator,
)
from learning.evaluation.execution import (
    ExecutionRecord,
)
from learning.evaluation.metrics import (
    MetricName,
    MetricResult,
    MetricSource,
)


BATCH_SIZE = 2


class FakeBatchJudge:
    """
    Fake semantic judge that deliberately fails batch 2 once.

    This verifies that a retry restores already-completed batches
    instead of evaluating them again.
    """

    def __init__(self) -> None:
        self.batch_calls: list[
            list[str]
        ] = []

        self.failed_once = False

    async def score_batch(
        self,
        executions: list[ExecutionRecord],
    ) -> dict[
        str,
        tuple[MetricResult, ...],
    ]:
        request_ids = [
            execution.request_id
            for execution in executions
        ]

        self.batch_calls.append(
            request_ids
        )

        # Deliberately fail the second batch only once.
        if (
            request_ids
            == [
                "req_3",
                "req_4",
            ]
            and not self.failed_once
        ):
            self.failed_once = True

            raise ValueError(
                "Deliberate batch failure."
            )

        return {
            execution.request_id: (
                MetricResult(
                    name=(
                        MetricName.TASK_FULFILLMENT
                    ),
                    source=MetricSource.MODEL,
                    value=1.0,
                    metadata={
                        "reasoning": (
                            "Fake semantic result."
                        ),
                    },
                ),
            )
            for execution in executions
        }


def make_execution(
    request_id: str,
) -> ExecutionRecord:
    """
    Create a minimal completed execution eligible for semantic
    evaluation.
    """

    return ExecutionRecord(
        request_id=request_id,
        query=(
            f"Test query for "
            f"{request_id}"
        ),
        status="completed",
        final_answer="Test answer.",
        duration_seconds=1.0,
        supervisor_llm_calls=1,
        tasks=(),
    )


async def run_test() -> None:
    checkpoint_id = (
        f"test_ckpt_{uuid.uuid4().hex}"
    )

    path = checkpoint_path(
        checkpoint_id
    )

    executions = [
        make_execution(
            "req_1"
        ),
        make_execution(
            "req_2"
        ),
        make_execution(
            "req_3"
        ),
        make_execution(
            "req_4"
        ),
        make_execution(
            "req_5"
        ),
    ]

    judge = FakeBatchJudge()

    evaluator = ExecutionEvaluator(
        judge=judge,  # type: ignore[arg-type]
    )

    try:
        # ====================================================
        # FIRST RUN
        # ====================================================

        print()
        print("=" * 70)
        print("FIRST RUN")
        print("=" * 70)

        try:
            await evaluator.evaluate_many(
                executions,
                batch_size=BATCH_SIZE,
                checkpoint_id=checkpoint_id,
            )

        except RuntimeError as exc:
            print()
            print(
                "Expected failure:"
            )
            print(
                str(exc)
            )

        else:
            raise AssertionError(
                "First run should have "
                "failed on batch 2."
            )

        # ----------------------------------------------------
        # Verify first run
        # ----------------------------------------------------

        expected_first_run_calls = [
            [
                "req_1",
                "req_2",
            ],
            [
                "req_3",
                "req_4",
            ],
        ]

        assert (
            judge.batch_calls
            == expected_first_run_calls
        ), (
            "Unexpected judge calls "
            "during first run: "
            f"{judge.batch_calls}"
        )

        print()
        print(
            "Judge calls after first run:"
        )

        for call in judge.batch_calls:
            print(
                f"  {call}"
            )

        # Batch 1 should have been persisted.
        assert path.exists(), (
            "Checkpoint should exist "
            "after failed evaluation."
        )

        print()
        print(
            f"Checkpoint exists: "
            f"{path}"
        )

        # ====================================================
        # RETRY
        # ====================================================

        print()
        print("=" * 70)
        print(
            "RETRY WITH SAME CHECKPOINT"
        )
        print("=" * 70)

        evaluations = (
            await evaluator.evaluate_many(
                executions,
                batch_size=BATCH_SIZE,
                checkpoint_id=checkpoint_id,
            )
        )

        # ====================================================
        # VERIFY RESUME
        # ====================================================

        # Expected behavior:
        #
        # FIRST RUN
        #
        # batch 1:
        #     req_1, req_2
        #     success
        #     checkpointed
        #
        # batch 2:
        #     req_3, req_4
        #     failure
        #
        # RETRY
        #
        # batch 1:
        #     restored
        #     NO judge call
        #
        # batch 2:
        #     req_3, req_4
        #     judge called again
        #
        # batch 3:
        #     req_5
        #     judge called

        expected_all_calls = [
            [
                "req_1",
                "req_2",
            ],
            [
                "req_3",
                "req_4",
            ],
            [
                "req_3",
                "req_4",
            ],
            [
                "req_5",
            ],
        ]

        assert (
            judge.batch_calls
            == expected_all_calls
        ), (
            "Checkpoint resume did not "
            "produce the expected judge "
            "call sequence.\n"
            f"Expected: "
            f"{expected_all_calls}\n"
            f"Actual: "
            f"{judge.batch_calls}"
        )

        print()
        print(
            "Judge calls after retry:"
        )

        for call in judge.batch_calls:
            print(
                f"  {call}"
            )

        # Batch 1 should appear exactly once.
        batch_one_calls = (
            judge.batch_calls.count(
                [
                    "req_1",
                    "req_2",
                ]
            )
        )

        assert batch_one_calls == 1, (
            "Batch 1 was evaluated more "
            "than once instead of being "
            "restored from checkpoint."
        )

        # Failed batch should appear twice:
        #
        # once for failure
        # once for successful retry

        batch_two_calls = (
            judge.batch_calls.count(
                [
                    "req_3",
                    "req_4",
                ]
            )
        )

        assert batch_two_calls == 2, (
            "Failed batch was not retried "
            "exactly once."
        )

        # ----------------------------------------------------
        # Verify final evaluations
        # ----------------------------------------------------

        assert len(
            evaluations
        ) == 5

        returned_request_ids = [
            evaluation.request_id
            for evaluation in evaluations
        ]

        expected_request_ids = [
            "req_1",
            "req_2",
            "req_3",
            "req_4",
            "req_5",
        ]

        assert (
            returned_request_ids
            == expected_request_ids
        ), (
            "Execution ordering changed.\n"
            f"Expected: "
            f"{expected_request_ids}\n"
            f"Actual: "
            f"{returned_request_ids}"
        )

        print()
        print(
            "Returned evaluations:"
        )

        for evaluation in evaluations:
            print(
                f"  "
                f"{evaluation.request_id}: "
                f"{len(evaluation.metrics)} "
                "metrics"
            )

        # ----------------------------------------------------
        # Verify evaluator does NOT own cleanup
        # ----------------------------------------------------

        assert path.exists(), (
            "Evaluator deleted the "
            "checkpoint prematurely."
        )

        print()
        print(
            "Checkpoint remains after "
            "evaluate_many(), as expected."
        )

        # ====================================================
        # SUCCESS
        # ====================================================

        print()
        print("=" * 70)
        print("TEST PASSED")
        print("=" * 70)

        print()
        print(
            "Confirmed:"
        )

        print(
            "  ✓ successful batch was "
            "checkpointed"
        )

        print(
            "  ✓ failed batch was not "
            "checkpointed"
        )

        print(
            "  ✓ completed batch was "
            "restored on retry"
        )

        print(
            "  ✓ restored batch did not "
            "call the judge again"
        )

        print(
            "  ✓ failed batch was retried"
        )

        print(
            "  ✓ remaining batch executed"
        )

        print(
            "  ✓ execution ordering was "
            "preserved"
        )

        print(
            "  ✓ evaluator left checkpoint "
            "cleanup to the tool layer"
        )

    finally:
        # The test owns cleanup because we are testing
        # ExecutionEvaluator directly rather than going through
        # evaluate_trace_batch().

        delete_checkpoint(
            checkpoint_id
        )

        assert not path.exists(), (
            "Checkpoint cleanup failed."
        )

        print()
        print(
            "Temporary test checkpoint "
            "removed."
        )


if __name__ == "__main__":
    asyncio.run(
        run_test()
    )