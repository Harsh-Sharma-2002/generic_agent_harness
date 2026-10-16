"""Model-based semantic scoring for KernelAI evaluations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from openai.types.chat import ChatCompletionMessageParam

from learning.evaluation.execution import ExecutionRecord
from learning.evaluation.metrics import (
    MetricName,
    MetricResult,
    MetricSource,
)
from worker.llm_caller import LLMCaller


# ============================================================
# Models
# ============================================================


@dataclass(frozen=True)
class GroundednessResult:
    """Claim-level groundedness evaluation."""

    supported_claims: int
    contradicted_claims: int
    unverifiable_claims: int

    @property
    def total_claims(self) -> int:
        return (
            self.supported_claims
            + self.contradicted_claims
            + self.unverifiable_claims
        )

    @property
    def supported_rate(self) -> float:
        if self.total_claims == 0:
            return 0.0

        return (
            self.supported_claims
            / self.total_claims
        )

    @property
    def contradicted_rate(self) -> float:
        if self.total_claims == 0:
            return 0.0

        return (
            self.contradicted_claims
            / self.total_claims
        )

    @property
    def unverifiable_rate(self) -> float:
        if self.total_claims == 0:
            return 0.0

        return (
            self.unverifiable_claims
            / self.total_claims
        )


# ============================================================
# Judge
# ============================================================


class SemanticJudge:
    """
    Semantic scoring functions for one KernelAI execution.

    The judge evaluates executions independently. It does not know
    about policy versions, candidates, releases, or learning
    decisions.
    """

    def __init__(
        self,
        max_tokens: int = 1024,
    ) -> None:
        if max_tokens < 1:
            raise ValueError(
                "max_tokens must be at least 1."
            )

        self.llm = LLMCaller()
        self.max_tokens = max_tokens

    # ========================================================
    # Task fulfillment
    # ========================================================

    async def score_task_fulfillment(
        self,
        execution: ExecutionRecord,
    ) -> MetricResult:
        """
        Score how completely the final answer fulfills the
        original user request.
        """

        _require_execution_answer(
            execution
        )

        prompt = f"""
Evaluate how completely the response fulfills the user's request.

USER REQUEST:
{execution.query}

RESPONSE:
{_serialize(execution.final_answer)}

Use this rubric:

1.0 = fully satisfies the request with no meaningful omission.
0.75 = satisfies most important requirements with minor omissions.
0.50 = partially satisfies the request but misses important requirements.
0.25 = addresses the request only weakly or incompletely.
0.0 = fails to answer the request.

Judge fulfillment only.

Do not consider latency, tool calls, token usage, or execution cost.

Return exactly:

{{
  "score": <number from 0.0 to 1.0>,
  "reasoning": "<brief explanation>"
}}
""".strip()

        result = await self._score(
            prompt
        )

        return MetricResult(
            name=MetricName.TASK_FULFILLMENT,
            source=MetricSource.MODEL,
            value=result["score"],
            metadata={
                "reasoning": result["reasoning"],
            },
        )

    # ========================================================
    # Scope overreach
    # ========================================================

    async def score_scope_overreach(
        self,
        execution: ExecutionRecord,
    ) -> MetricResult:
        """
        Score how much the final answer goes beyond what the
        user requested.

        Lower is better.
        """

        _require_execution_answer(
            execution
        )

        prompt = f"""
Evaluate whether the response unnecessarily goes beyond the user's request.

USER REQUEST:
{execution.query}

RESPONSE:
{_serialize(execution.final_answer)}

Use this rubric:

0.0 = tightly scoped to the request.
0.25 = small amount of unnecessary material.
0.50 = substantial unnecessary material.
0.75 = response is significantly broader than requested.
1.0 = response largely ignores the requested scope.

Do not penalize useful context that is necessary to answer correctly.

Return exactly:

{{
  "score": <number from 0.0 to 1.0>,
  "reasoning": "<brief explanation>"
}}
""".strip()

        result = await self._score(
            prompt
        )

        return MetricResult(
            name=MetricName.SCOPE_OVERREACH,
            source=MetricSource.MODEL,
            value=result["score"],
            metadata={
                "reasoning": result["reasoning"],
            },
        )

    # ========================================================
    # Sufficiency stopping
    # ========================================================

    async def score_sufficiency_stopping(
        self,
        execution: ExecutionRecord,
    ) -> MetricResult:
        """
        Evaluate whether KernelAI continued gathering information
        after enough evidence existed to answer the request.

        Higher is better.
        """

        _require_execution_answer(
            execution
        )

        trajectory = _serialize_trajectory(
            execution
        )

        prompt = f"""
Evaluate whether the execution stopped gathering information once
it had enough evidence to answer the user's request well.

USER REQUEST:
{execution.query}

EXECUTION TRAJECTORY:
{trajectory}

FINAL RESPONSE:
{_serialize(execution.final_answer)}

Use this rubric:

1.0 = stopped at an appropriate point once sufficient evidence existed.
0.75 = mostly efficient, with a small amount of unnecessary extra work.
0.50 = noticeable unnecessary work after sufficient evidence existed.
0.25 = substantial redundant investigation.
0.0 = continued investigating extensively despite already having enough evidence.

Do not penalize additional work that materially improved correctness,
coverage, verification, or confidence.

Return exactly:

{{
  "score": <number from 0.0 to 1.0>,
  "reasoning": "<brief explanation>"
}}
""".strip()

        result = await self._score(
            prompt
        )

        return MetricResult(
            name=MetricName.SUFFICIENCY_STOPPING,
            source=MetricSource.MODEL,
            value=result["score"],
            metadata={
                "reasoning": result["reasoning"],
            },
        )

    # ========================================================
    # Groundedness
    # ========================================================

    async def score_groundedness(
        self,
        execution: ExecutionRecord,
    ) -> tuple[MetricResult, ...]:
        """
        Classify factual claims in the final answer against the
        evidence available in tool results.

        The model performs claim classification. Python derives
        the final rates.
        """

        _require_execution_answer(
            execution
        )

        evidence = _serialize_evidence(
            execution
        )

        prompt = f"""
Evaluate the factual claims in the response against ONLY the evidence
provided below.

USER REQUEST:
{execution.query}

RESPONSE:
{_serialize(execution.final_answer)}

AVAILABLE EVIDENCE:
{evidence}

For each factual claim in the response, classify it as exactly one of:

supported
    The available evidence supports the claim.

contradicted
    The available evidence contradicts the claim.

unverifiable
    The available evidence is insufficient to determine whether
    the claim is true.

Do not use outside knowledge.

Return exactly:

{{
  "claims": [
    {{
      "claim": "<factual claim>",
      "classification": "supported|contradicted|unverifiable"
    }}
  ]
}}
""".strip()

        response = await self._call(
            prompt
        )

        claims = response.get(
            "claims"
        )

        if not isinstance(
            claims,
            list,
        ):
            raise ValueError(
                "Groundedness judge must return a claims list."
            )

        supported = 0
        contradicted = 0
        unverifiable = 0

        for claim in claims:
            if not isinstance(
                claim,
                dict,
            ):
                raise ValueError(
                    "Groundedness claim must be an object."
                )

            classification = claim.get(
                "classification"
            )

            if classification == "supported":
                supported += 1

            elif classification == "contradicted":
                contradicted += 1

            elif classification == "unverifiable":
                unverifiable += 1

            else:
                raise ValueError(
                    "Unknown groundedness classification."
                )

        result = GroundednessResult(
            supported_claims=supported,
            contradicted_claims=contradicted,
            unverifiable_claims=unverifiable,
        )

        metadata = {
            "supported_claims": supported,
            "contradicted_claims": contradicted,
            "unverifiable_claims": unverifiable,
            "total_claims": result.total_claims,
            "claims": claims,
        }

        return (
            MetricResult(
                name=MetricName.SUPPORTED_CLAIM_RATE,
                source=MetricSource.MODEL,
                value=result.supported_rate,
                metadata=metadata,
            ),
            MetricResult(
                name=MetricName.CONTRADICTED_CLAIM_RATE,
                source=MetricSource.MODEL,
                value=result.contradicted_rate,
                metadata=metadata,
            ),
            MetricResult(
                name=MetricName.UNVERIFIABLE_CLAIM_RATE,
                source=MetricSource.MODEL,
                value=result.unverifiable_rate,
                metadata=metadata,
            ),
        )

    # ========================================================
    # Shared scoring
    # ========================================================

    async def _score(
        self,
        prompt: str,
    ) -> dict[str, Any]:
        """Run and validate one scalar semantic score."""

        result = await self._call(
            prompt
        )

        score = result.get(
            "score"
        )

        reasoning = result.get(
            "reasoning"
        )

        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
        ):
            raise ValueError(
                "Semantic judge score must be numeric."
            )

        score = float(
            score
        )

        if not 0.0 <= score <= 1.0:
            raise ValueError(
                "Semantic judge score must be between "
                "0.0 and 1.0."
            )

        if (
            not isinstance(reasoning, str)
            or not reasoning.strip()
        ):
            raise ValueError(
                "Semantic judge reasoning must be "
                "a non-empty string."
            )

        return {
            "score": score,
            "reasoning": reasoning.strip(),
        }

    async def _call(
        self,
        prompt: str,
    ) -> dict[str, Any]:
        """Call the model and parse one JSON response."""

        messages: list[
            ChatCompletionMessageParam
        ] = [
            {
                "role": "system",
                "content": (
                    "You are an evaluation component inside "
                    "KernelAI. Evaluate only the evidence supplied "
                    "to you. Follow the requested scoring rubric "
                    "exactly and return only valid JSON."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]

        response = await self.llm.call(
            messages=messages,
            max_tokens=self.max_tokens,
        )

        content = response.content

        if (
            content is None
            or not content.strip()
        ):
            raise ValueError(
                "Semantic judge returned empty content."
            )

        try:
            parsed = json.loads(
                content
            )

        except json.JSONDecodeError as exc:
            raise ValueError(
                "Semantic judge returned invalid JSON."
            ) from exc

        if not isinstance(
            parsed,
            dict,
        ):
            raise ValueError(
                "Semantic judge response must be "
                "a JSON object."
            )

        return parsed


# ============================================================
# Evaluation input
# ============================================================


def _require_execution_answer(
    execution: ExecutionRecord,
) -> None:
    """Validate minimum evidence required for semantic scoring."""

    if not execution.query.strip():
        raise ValueError(
            "Execution has no original query."
        )

    if execution.final_answer is None:
        raise ValueError(
            "Execution has no final answer."
        )


def _serialize_trajectory(
    execution: ExecutionRecord,
) -> str:
    """Serialize the execution trajectory for semantic judging."""

    trajectory: list[
        dict[str, Any]
    ] = []

    for task in execution.tasks:
        trajectory.append(
            {
                "task": task.description,
                "skills": list(task.skills),
                "tool_calls": [
                    {
                        "tool": call.tool_name,
                        "arguments": call.arguments,
                        "result": call.result,
                        "success": call.success,
                    }
                    for call in task.tool_calls
                ],
            }
        )

    return _serialize(
        trajectory
    )


def _serialize_evidence(
    execution: ExecutionRecord,
) -> str:
    """Serialize tool results as evidence for groundedness."""

    evidence: list[
        dict[str, Any]
    ] = []

    for task in execution.tasks:
        for call in task.tool_calls:
            if not call.success:
                continue

            evidence.append(
                {
                    "tool": call.tool_name,
                    "arguments": call.arguments,
                    "result": call.result,
                }
            )

    return _serialize(
        evidence
    )


def _serialize(
    value: Any,
) -> str:
    """Serialize values for model evaluation."""

    if isinstance(value, str):
        return value

    return json.dumps(
        value,
        default=str,
        ensure_ascii=False,
        indent=2,
    )