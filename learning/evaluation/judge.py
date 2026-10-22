"""Model-based semantic scoring for KernelAI evaluations."""

from __future__ import annotations

import json
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
# Structured output schemas
# ============================================================


def _response_format(
    name: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """
    Build one OpenAI-style strict JSON Schema response format
    for the judge's LLM calls.
    """

    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": schema,
        },
    }


_SCALAR_RESPONSE_FORMAT = _response_format(
    "scalar_score",
    {
        "type": "object",
        "properties": {
            "score": {
                "type": "number",
            },
            "reasoning": {
                "type": "string",
            },
        },
        "required": [
            "score",
            "reasoning",
        ],
        "additionalProperties": False,
    },
)


_REQUIREMENT_COVERAGE_RESPONSE_FORMAT = _response_format(
    "requirement_coverage",
    {
        "type": "object",
        "properties": {
            "requirements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "requirement": {
                            "type": "string",
                        },
                        "status": {
                            "type": "string",
                            "enum": [
                                "satisfied",
                                "partial",
                                "unsatisfied",
                            ],
                        },
                    },
                    "required": [
                        "requirement",
                        "status",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "requirements",
        ],
        "additionalProperties": False,
    },
)


_POLICY_COMPLIANCE_RESPONSE_FORMAT = _response_format(
    "policy_compliance",
    {
        "type": "object",
        "properties": {
            "requirements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "requirement": {
                            "type": "string",
                        },
                        "status": {
                            "type": "string",
                            "enum": [
                                "compliant",
                                "violated",
                                "not_applicable",
                            ],
                        },
                    },
                    "required": [
                        "requirement",
                        "status",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "requirements",
        ],
        "additionalProperties": False,
    },
)


_CLAIMS_RESPONSE_FORMAT = _response_format(
    "groundedness_claims",
    {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {
                            "type": "string",
                        },
                        "classification": {
                            "type": "string",
                            "enum": [
                                "supported",
                                "contradicted",
                                "unverifiable",
                            ],
                        },
                    },
                    "required": [
                        "claim",
                        "classification",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "claims",
        ],
        "additionalProperties": False,
    },
)


class SemanticJudge:
    """
    Semantic scoring functions for one KernelAI execution.

    The judge evaluates one execution independently.

    It does not know about policy versions, candidates, releases,
    or learning decisions.
    """

    def __init__(
        self,
        max_tokens: int = 2048,
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
        Score whether the final answer actually fulfills the
        user's overall request.

        This is intentionally holistic. Requirement-level
        completeness is measured separately.
        """

        _require_answer(
            execution
        )

        prompt = f"""
Evaluate how well the response fulfills the user's overall request.

USER REQUEST:
{execution.query}

RESPONSE:
{_serialize(execution.final_answer)}

Use this rubric:

1.0 = fully fulfills the user's goal.
0.75 = substantially fulfills the goal with minor weaknesses.
0.50 = partially fulfills the goal but has important deficiencies.
0.25 = weakly addresses the goal.
0.0 = does not meaningfully fulfill the goal.

Judge overall usefulness and correctness relative to the request.

Do not score requirement-by-requirement completeness here.
That is evaluated separately.

Do not consider latency, tool count, LLM count, token usage,
or execution cost.

Return exactly:

{{
  "score": <number from 0.0 to 1.0>,
  "reasoning": "<brief explanation>"
}}
""".strip()

        result = await self._scalar_score(
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
    # Requirement coverage / completeness
    # ========================================================

    async def score_requirement_coverage(
        self,
        execution: ExecutionRecord,
    ) -> MetricResult:
        """
        Extract the requirements implied by the user request and
        classify whether each was satisfied.

        Python derives the final coverage score:

            satisfied = 1.0
            partial = 0.5
            unsatisfied = 0.0
        """

        _require_answer(
            execution
        )

        prompt = f"""
Determine how completely the response covers the requirements of
the user's request.

USER REQUEST:
{execution.query}

RESPONSE:
{_serialize(execution.final_answer)}

First identify the distinct requirements in the user's request.

Include:
- explicitly requested information,
- explicitly requested actions,
- requested constraints such as format, scope, or conciseness,
- requirements that are directly necessary to satisfy the request.

Do not invent unrelated requirements.

For each requirement classify its status as exactly one of:

satisfied
    The response adequately satisfies the requirement.

partial
    The response addresses the requirement but incompletely.

unsatisfied
    The response does not meaningfully satisfy the requirement.

Return exactly:

{{
  "requirements": [
    {{
      "requirement": "<requirement>",
      "status": "satisfied|partial|unsatisfied"
    }}
  ]
}}
""".strip()

        result = await self._call(
            prompt,
            response_format=(
                _REQUIREMENT_COVERAGE_RESPONSE_FORMAT
            ),
        )

        requirements = _require_list(
            result,
            "requirements",
        )

        satisfied = 0
        partial = 0
        unsatisfied = 0

        for requirement in requirements:
            if not isinstance(
                requirement,
                dict,
            ):
                raise ValueError(
                    "Requirement evaluation entries "
                    "must be objects."
                )

            text = requirement.get(
                "requirement"
            )

            status = requirement.get(
                "status"
            )

            if (
                not isinstance(text, str)
                or not text.strip()
            ):
                raise ValueError(
                    "Requirement must contain "
                    "non-empty text."
                )

            if status == "satisfied":
                satisfied += 1

            elif status == "partial":
                partial += 1

            elif status == "unsatisfied":
                unsatisfied += 1

            else:
                raise ValueError(
                    "Unknown requirement status."
                )

        total = (
            satisfied
            + partial
            + unsatisfied
        )

        if total == 0:
            raise ValueError(
                "Requirement judge returned no requirements."
            )

        score = (
            satisfied
            + (0.5 * partial)
        ) / total

        return MetricResult(
            name=MetricName.REQUIREMENT_COVERAGE,
            source=MetricSource.MODEL,
            value=score,
            metadata={
                "satisfied": satisfied,
                "partial": partial,
                "unsatisfied": unsatisfied,
                "total": total,
                "requirements": requirements,
            },
        )

    # ========================================================
    # Policy compliance
    # ========================================================

    async def score_policy_compliance(
        self,
        execution: ExecutionRecord,
        *,
        policy: str,
    ) -> tuple[MetricResult, MetricResult]:
        """
        Evaluate whether the execution complied with its supplied
        policy / prompt.

        The model identifies applicable policy requirements and
        classifies each one.

        Python calculates both:
        - normalized compliance rate
        - absolute policy violation count
        """

        _require_answer(
            execution
        )

        if not policy.strip():
            raise ValueError(
                "Policy compliance requires "
                "a non-empty policy."
            )

        trajectory = _serialize_trajectory(
            execution
        )

        prompt = f"""
Evaluate whether this execution complied with the supplied policy.

POLICY:
{policy}

USER REQUEST:
{execution.query}

EXECUTION TRAJECTORY:
{trajectory}

FINAL RESPONSE:
{_serialize(execution.final_answer)}

Identify concrete policy requirements that are applicable to this
execution.

For each applicable requirement classify it as exactly one of:

compliant
    The execution followed the requirement.

violated
    The execution clearly violated the requirement.

not_applicable
    The policy requirement does not apply to this execution.

Use only observable evidence from the trajectory and final response.

Do not mark something violated merely because the trace does not
prove it occurred unless the policy explicitly requires observable
behavior that is absent.

Return exactly:

{{
  "requirements": [
    {{
      "requirement": "<policy requirement>",
      "status": "compliant|violated|not_applicable"
    }}
  ]
}}
""".strip()

        result = await self._call(
            prompt,
            response_format=(
                _POLICY_COMPLIANCE_RESPONSE_FORMAT
            ),
        )

        requirements = _require_list(
            result,
            "requirements",
        )

        compliant = 0
        violated = 0
        not_applicable = 0

        for requirement in requirements:
            if not isinstance(
                requirement,
                dict,
            ):
                raise ValueError(
                    "Policy evaluation entries "
                    "must be objects."
                )

            text = requirement.get(
                "requirement"
            )

            status = requirement.get(
                "status"
            )

            if (
                not isinstance(text, str)
                or not text.strip()
            ):
                raise ValueError(
                    "Policy requirement must contain "
                    "non-empty text."
                )

            if status == "compliant":
                compliant += 1

            elif status == "violated":
                violated += 1

            elif status == "not_applicable":
                not_applicable += 1

            else:
                raise ValueError(
                    "Unknown policy compliance status."
                )

        applicable = (
            compliant
            + violated
        )

        if applicable == 0:
            compliance_rate = 1.0

        else:
            compliance_rate = (
                compliant
                / applicable
            )

        metadata = {
            "compliant": compliant,
            "violated": violated,
            "not_applicable": not_applicable,
            "applicable": applicable,
            "requirements": requirements,
        }

        return (
            MetricResult(
                name=MetricName.POLICY_COMPLIANCE,
                source=MetricSource.MODEL,
                value=compliance_rate,
                metadata=metadata,
            ),
            MetricResult(
                name=MetricName.POLICY_VIOLATIONS,
                source=MetricSource.MODEL,
                value=violated,
                metadata=metadata,
            ),
        )

    # ========================================================
    # Groundedness
    # ========================================================

    async def score_groundedness(
        self,
        execution: ExecutionRecord,
    ) -> tuple[
        MetricResult,
        MetricResult,
        MetricResult,
    ]:
        """
        Classify factual claims in the final answer against the
        evidence available from successful tool calls.

        The model performs semantic classification.
        Python calculates the resulting rates.
        """

        _require_answer(
            execution
        )

        evidence = _serialize_evidence(
            execution
        )

        prompt = f"""
Evaluate the factual claims in the response against ONLY the
available evidence.

USER REQUEST:
{execution.query}

RESPONSE:
{_serialize(execution.final_answer)}

AVAILABLE EVIDENCE:
{evidence}

Identify independently verifiable factual claims in the response.

For each claim classify it as exactly one of:

supported
    The available evidence supports the claim.

contradicted
    The available evidence contradicts the claim.

unverifiable
    The available evidence is insufficient to determine whether
    the claim is supported.

Do not use outside knowledge.

Do not classify opinions, formatting statements, or purely
subjective recommendations as factual claims.

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

        result = await self._call(
            prompt,
            response_format=(
                _CLAIMS_RESPONSE_FORMAT
            ),
        )

        claims = _require_list(
            result,
            "claims",
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
                    "Groundedness claim entries "
                    "must be objects."
                )

            text = claim.get(
                "claim"
            )

            classification = claim.get(
                "classification"
            )

            if (
                not isinstance(text, str)
                or not text.strip()
            ):
                raise ValueError(
                    "Groundedness claim must contain "
                    "non-empty text."
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

        total = (
            supported
            + contradicted
            + unverifiable
        )

        if total == 0:
            supported_rate = 1.0
            contradicted_rate = 0.0
            unverifiable_rate = 0.0

        else:
            supported_rate = (
                supported / total
            )

            contradicted_rate = (
                contradicted / total
            )

            unverifiable_rate = (
                unverifiable / total
            )

        metadata = {
            "supported_claims": supported,
            "contradicted_claims": contradicted,
            "unverifiable_claims": unverifiable,
            "total_claims": total,
            "claims": claims,
        }

        return (
            MetricResult(
                name=MetricName.SUPPORTED_CLAIM_RATE,
                source=MetricSource.MODEL,
                value=supported_rate,
                metadata=metadata,
            ),
            MetricResult(
                name=MetricName.CONTRADICTED_CLAIM_RATE,
                source=MetricSource.MODEL,
                value=contradicted_rate,
                metadata=metadata,
            ),
            MetricResult(
                name=MetricName.UNVERIFIABLE_CLAIM_RATE,
                source=MetricSource.MODEL,
                value=unverifiable_rate,
                metadata=metadata,
            ),
        )

    # ========================================================
    # Scope overreach
    # ========================================================

    async def score_scope_overreach(
        self,
        execution: ExecutionRecord,
    ) -> MetricResult:
        """
        Score unnecessary expansion beyond the user's request.

        Lower is better.
        """

        _require_answer(
            execution
        )

        prompt = f"""
Evaluate how much the response unnecessarily exceeds the scope of
the user's request.

USER REQUEST:
{execution.query}

RESPONSE:
{_serialize(execution.final_answer)}

Use this rubric:

0.0 = tightly scoped to the request.
0.25 = small amount of unnecessary material.
0.50 = substantial unnecessary material.
0.75 = significantly broader than requested.
1.0 = largely ignores the requested scope.

Do not penalize context that is genuinely useful or necessary for
answering the request correctly.

Do not reward brevity by itself.

Return exactly:

{{
  "score": <number from 0.0 to 1.0>,
  "reasoning": "<brief explanation>"
}}
""".strip()

        result = await self._scalar_score(
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
        Score whether the execution stopped gathering information
        once enough evidence existed.

        Higher is better.
        """

        _require_answer(
            execution
        )

        trajectory = _serialize_trajectory(
            execution
        )

        prompt = f"""
Evaluate whether the execution stopped gathering information once
it had sufficient evidence to answer the user's request well.

USER REQUEST:
{execution.query}

EXECUTION TRAJECTORY:
{trajectory}

FINAL RESPONSE:
{_serialize(execution.final_answer)}

Use this rubric:

1.0 = stopped at an appropriate point once sufficient evidence existed.
0.75 = mostly appropriate stopping with minor unnecessary work.
0.50 = noticeable unnecessary work after sufficient evidence existed.
0.25 = substantial redundant investigation.
0.0 = continued investigating extensively despite already having
      sufficient evidence.

Do not penalize additional work that materially improved correctness,
coverage, verification, or confidence.

Do not infer redundancy merely from the number of tool calls.
Judge whether later calls added useful information.

Return exactly:

{{
  "score": <number from 0.0 to 1.0>,
  "reasoning": "<brief explanation>"
}}
""".strip()

        result = await self._scalar_score(
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
    # Shared scalar scoring
    # ========================================================

    async def _scalar_score(
        self,
        prompt: str,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run and validate one scalar semantic score."""

        result = await self._call(
            prompt,
            response_format=(
                response_format
                if response_format is not None
                else _SCALAR_RESPONSE_FORMAT
            ),
        )

        score = result.get(
            "score"
        )

        reasoning = result.get(
            "reasoning"
        )

        if (
            not isinstance(
                score,
                (int, float),
            )
            or isinstance(
                score,
                bool,
            )
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

  
    # LLM call
    

    async def _call(
        self,
        prompt: str,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Call the semantic judge and parse its JSON response.

        When response_format is None the judge still requests
        generic JSON-object mode.
        """

        if response_format is None:
            response_format = {
                "type": "json_object",
            }

        messages: list[
            ChatCompletionMessageParam
        ] = [
            {
                "role": "system",
                "content": (
                    "You are a scoring component inside "
                    "KernelAI's offline evaluation system. "
                    "Evaluate only the supplied execution evidence. "
                    "Do not make learning decisions or recommend "
                    "policy changes. Follow the requested rubric "
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
            response_format=response_format,
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
                "Semantic judge response must "
                "be a JSON object."
            )

        return parsed


# ============================================================
# Validation
# ============================================================


def _require_answer(
    execution: ExecutionRecord,
) -> None:
    """Validate evidence required for semantic scoring."""

    if not execution.query.strip():
        raise ValueError(
            "Execution has no original query."
        )

    if execution.final_answer is None:
        raise ValueError(
            "Execution has no final answer."
        )


def _require_list(
    result: dict[str, Any],
    key: str,
) -> list[Any]:
    """Require a list field from a judge response."""

    value = result.get(
        key
    )

    if not isinstance(
        value,
        list,
    ):
        raise ValueError(
            f"Semantic judge field {key!r} "
            "must be a list."
        )

    return value


# ============================================================
# Evidence serialization
# ============================================================


def _serialize_trajectory(
    execution: ExecutionRecord,
) -> str:
    """
    Serialize Task and tool activity needed for trajectory-based
    semantic scoring.
    """

    trajectory: list[
        dict[str, Any]
    ] = []

    for task in execution.tasks:
        trajectory.append(
            {
                "task": task.description,
                "skills": list(
                    task.skills
                ),
                "status": task.status,
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
    """Serialize successful tool results as grounding evidence."""

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
    """Serialize values for semantic evaluation."""

    if isinstance(
        value,
        str,
    ):
        return value

    return json.dumps(
        value,
        default=str,
        ensure_ascii=False,
        indent=2,
    )