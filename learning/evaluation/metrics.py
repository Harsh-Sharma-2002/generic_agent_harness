"""Metric models for KernelAI offline policy evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class MetricSource(str, Enum):
    """
    Identifies how an evaluation metric was produced.
    """

    DETERMINISTIC = "deterministic"
    MODEL = "model"


class MetricName(str, Enum):
    """
    Metrics supported by the KernelAI offline evaluator.
    """


    # Outcome / validity


    COMPLETION = "completion"
    VERIFIED_SUCCESS = "verified_success"

    SCHEMA_VIOLATIONS = "schema_violations"
    POLICY_VIOLATIONS = "policy_violations"
    TIMEOUTS = "timeouts"


    # Reliability


    POLICY_FAILURES = "policy_failures"
    PROVIDER_FAILURES = "provider_failures"
    RUNTIME_FAILURES = "runtime_failures"

 
    # Resource usage
 

    TOOL_CALLS = "tool_calls"
    LLM_CALLS = "llm_calls"

    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"

    LATENCY_SECONDS = "latency_seconds"

  
    # Deterministic trajectory waste
   

    EXACT_DUPLICATE_CALLS = "exact_duplicate_calls"
    NEAR_DUPLICATE_CALLS = "near_duplicate_calls"
    UNCHANGED_RETRIES = "unchanged_retries"
    UNUSED_RESULTS = "unused_results"

    # Semantic evaluation
 

    TASK_FULFILLMENT = "task_fulfillment"

    SUPPORTED_CLAIM_RATE = "supported_claim_rate"
    CONTRADICTED_CLAIM_RATE = "contradicted_claim_rate"
    UNVERIFIABLE_CLAIM_RATE = "unverifiable_claim_rate"

    SCOPE_OVERREACH = "scope_overreach"
    SUFFICIENCY_STOPPING = "sufficiency_stopping"

 
    # Supervisor diagnostics
   

    TASK_COUNT = "task_count"
    REDUNDANT_TASK_RATE = "redundant_task_rate"
    REQUIREMENT_COVERAGE = "requirement_coverage"
    PARALLELISM_FACTOR = "parallelism_factor"
    CRITICAL_PATH_SECONDS = "critical_path_seconds"
    CROSS_TASK_DUPLICATE_WORK = "cross_task_duplicate_work"


@dataclass(frozen=True)
class MetricResult:
    """
    Result of one evaluation metric.

    Metric values remain in their native units rather than being
    collapsed into a single artificial score.
    """

    name: MetricName
    source: MetricSource
    value: float | int | bool

    metadata: dict[str, Any] | None = None