"""Manual smoke test for the batched semantic judge."""

from __future__ import annotations

import asyncio
from pathlib import Path

from learning.evaluation.batch_judge import SemanticJudge
from learning.evaluation.loader import load_executions


TRACE_PATH = Path(
    "learning/data/traces/production/runtime.jsonl"
)

TEST_EXECUTIONS = 10


async def main() -> None:
    executions = load_executions(
        TRACE_PATH
    )

    eligible = [
        execution
        for execution in executions
        if (
            execution.query.strip()
            and execution.final_answer is not None
        )
    ]

    selected = eligible[
        :TEST_EXECUTIONS
    ]

    print(
        f"Loaded {len(executions)} executions."
    )

    print(
        f"Testing batch judge on "
        f"{len(selected)} executions."
    )

    for execution in selected:
        print(
            f"  - {execution.request_id}"
        )

    judge = SemanticJudge(
        max_tokens=8192
    )

    results = await judge.score_batch(
        selected
    )

    print(
        "\nBatch judge completed.\n"
    )

    for execution in selected:
        request_id = (
            execution.request_id
        )

        print(
            "=" * 80
        )

        print(
            f"REQUEST: {request_id}"
        )

        metrics = results[
            request_id
        ]

        for metric in metrics:
            print(
                f"{metric.name}: "
                f"{metric.value}"
            )

            if metric.metadata:
                print(
                    f"  metadata: "
                    f"{metric.metadata}"
                )


if __name__ == "__main__":
    asyncio.run(
        main()
    )
