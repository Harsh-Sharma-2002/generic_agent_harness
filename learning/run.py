"""Entry point for one KernelAI offline learning cycle.

This module runs the complete automated portion of offline learning:

production traces
    -> evaluation
    -> diagnosis
    -> policy inspection
    -> optional candidate creation

Production release is intentionally NOT part of this pipeline.
Candidates must be promoted separately through learning/release.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from learning.config.loader import load_learning_config
from learning.supervisor.supervisor import LearningSupervisor


DEFAULT_MAX_ITERATIONS = 15


async def run_learning_cycle(
    *,
    instruction: str | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    verbose: bool = False,
    local: bool = False,
) -> dict[str, Any]:
    """
    Run one complete offline learning cycle.

    The Learning Supervisor will:

    1. evaluate historical production traces,
    2. inspect aggregate metric trends,
    3. investigate bounded outliers when needed,
    4. inspect individual executions only when necessary,
    5. identify a plausibly responsible policy,
    6. read the current policy,
    7. create an isolated candidate only when justified.

    The pipeline cannot release or activate a candidate.

    Args:
        instruction:
            Optional additional instruction for this learning run.

            When omitted, the Learning Supervisor performs its normal
            evidence-driven learning workflow.

        max_iterations:
            Maximum number of Learning Supervisor agent iterations.

        verbose:
            Enable verbose worker output.

        local:
            Use KernelAI's local model path when supported.

    Returns:
        Final Learning Supervisor result.
    """

    config = load_learning_config()

    if not config.enabled:
        raise RuntimeError(
            "Offline learning is disabled in learning.yaml."
        )

    if max_iterations < 1:
        raise ValueError(
            "max_iterations must be at least 1."
        )

    supervisor = LearningSupervisor(
        max_iterations=max_iterations,
        verbose=verbose,
        local=local,
    )

    return await supervisor.run(
        instruction=instruction
    )


def main() -> None:
    """
    Command-line entry point for one offline learning cycle.

    This command never releases candidates into production.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Run one KernelAI offline learning cycle."
        )
    )

    parser.add_argument(
        "--instruction",
        type=str,
        default=None,
        help=(
            "Optional additional instruction for the Learning "
            "Supervisor."
        ),
    )

    parser.add_argument(
        "--max-iterations",
        type=int,
        default=DEFAULT_MAX_ITERATIONS,
        help=(
            "Maximum Learning Supervisor iterations "
            f"(default: {DEFAULT_MAX_ITERATIONS})."
        ),
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose Learning Supervisor output.",
    )

    parser.add_argument(
        "--local",
        action="store_true",
        help="Use KernelAI's local model path when supported.",
    )

    args = parser.parse_args()

    result = asyncio.run(
        run_learning_cycle(
            instruction=args.instruction,
            max_iterations=args.max_iterations,
            verbose=args.verbose,
            local=args.local,
        )
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
