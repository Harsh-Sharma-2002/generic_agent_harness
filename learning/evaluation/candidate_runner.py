"""Isolated candidate execution for KernelAI offline evaluation.

This module executes a fixed EvaluationWorkload using a candidate
policy without modifying the production policy file.

Responsibilities:

- accept an immutable evaluation workload,
- execute every case through an isolated KernelAI runtime,
- apply one candidate policy override,
- collect the resulting request outputs,
- preserve source-request provenance.

This module does not score executions, compare versions, release
candidates, or modify production policy files.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from learning.evaluation.workload import (
    EvaluationCase,
    EvaluationWorkload,
)


@dataclass(frozen=True)
class CandidateExecutionResult:
    """
    Result of replaying one evaluation case under a candidate policy.
    """

    source_request_id: str
    evaluation_request_id: str
    query: str

    status: str
    result: dict[str, Any] | None
    error: str | None = None


@dataclass(frozen=True)
class CandidateRun:
    """
    Complete execution outcome for one candidate workload run.
    """

    policy_name: str
    candidate_id: str

    executions: tuple[
        CandidateExecutionResult,
        ...
    ]

    @property
    def execution_count(self) -> int:
        """Return the number of replayed evaluation cases."""

        return len(
            self.executions
        )


class CandidateRunner:
    """
    Execute evaluation workloads using isolated candidate policies.

    Candidate policy content is supplied explicitly. Production policy
    files are never modified by this runner.
    """

    def __init__(
        self,
        *,
        max_concurrent_requests: int = 5,
    ) -> None:
        if max_concurrent_requests < 1:
            raise ValueError(
                "max_concurrent_requests must be at least 1."
            )

        self.max_concurrent_requests = (
            max_concurrent_requests
        )

    async def run(
        self,
        *,
        workload: EvaluationWorkload,
        policy_name: str,
        candidate_id: str,
        candidate_content: str,
    ) -> CandidateRun:
        """
        Execute every workload case under one candidate policy.

        Args:
            workload:
                Fixed historical request workload to replay.

            policy_name:
                Production policy being experimentally replaced.

            candidate_id:
                Identifier of the candidate being evaluated.

            candidate_content:
                Complete candidate policy text.

        Returns:
            CandidateRun containing one result per workload case.

        Important:
            This method must never overwrite the production policy.
        """

        if not workload.cases:
            raise ValueError(
                "Candidate workload must not be empty."
            )

        if not policy_name.strip():
            raise ValueError(
                "policy_name must not be empty."
            )

        if not candidate_id.strip():
            raise ValueError(
                "candidate_id must not be empty."
            )

        if not candidate_content.strip():
            raise ValueError(
                "candidate_content must not be empty."
            )

        semaphore = asyncio.Semaphore(
            self.max_concurrent_requests
        )

        async def execute_case(
            case: EvaluationCase,
        ) -> CandidateExecutionResult:
            async with semaphore:
                return await self._execute_case(
                    case=case,
                    policy_name=policy_name,
                    candidate_content=candidate_content,
                )

        results = await asyncio.gather(
            *(
                execute_case(case)
                for case in workload.cases
            )
        )

        return CandidateRun(
            policy_name=policy_name,
            candidate_id=candidate_id,
            executions=tuple(
                results
            ),
        )

    async def _execute_case(
        self,
        *,
        case: EvaluationCase,
        policy_name: str,
        candidate_content: str,
    ) -> CandidateExecutionResult:
        """
        Execute one workload case using the candidate policy.

        Runtime wiring is intentionally isolated in this method so
        candidate-policy injection has one implementation boundary.
        """

        evaluation_request_id = (
            _new_evaluation_request_id()
        )

        try:
            result = await _run_candidate_request(
                request_id=evaluation_request_id,
                query=case.query,
                policy_name=policy_name,
                candidate_content=candidate_content,
            )

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            return CandidateExecutionResult(
                source_request_id=(
                    case.source_request_id
                ),
                evaluation_request_id=(
                    evaluation_request_id
                ),
                query=case.query,
                status="failed",
                result=None,
                error=(
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
            )

        return CandidateExecutionResult(
            source_request_id=(
                case.source_request_id
            ),
            evaluation_request_id=(
                evaluation_request_id
            ),
            query=case.query,
            status="completed",
            result=result,
        )


async def _run_candidate_request(
    *,
    request_id: str,
    query: str,
    policy_name: str,
    candidate_content: str,
) -> dict[str, Any]:
    """
    Execute one request with an in-memory candidate policy override.

    IMPORTANT:
        This function is the only unresolved runtime-integration seam
        in the candidate runner.

        It must construct an isolated KernelAI execution in which
        `policy_name` resolves to `candidate_content` instead of the
        production policy text.

        It must NOT:
        - overwrite the production .md file,
        - mutate the global SkillRegistry,
        - affect normal API requests,
        - leak the candidate into other concurrent requests.

    The implementation should use KernelAI's existing worker/factory/
    supervisor dependency-injection boundaries rather than temporary
    filesystem replacement.
    """

    raise NotImplementedError(
        "Candidate runtime policy injection is not wired yet."
    )


def _new_evaluation_request_id() -> str:
    """
    Generate a request ID that is visibly isolated from production
    request identifiers.
    """

    return (
        f"eval_req_{uuid.uuid4().hex}"
    )