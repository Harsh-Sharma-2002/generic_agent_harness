"""Canonical Task Worker result protocol.

``WorkerTaskResult`` is the single structured result a Task Worker
attempt can produce. It is the one place lifecycle, accumulated work,
and escalation are defined for the Worker/Executor boundary.

The JSON schema used for model finalization is derived from this model,
so the runtime contract and the LLM contract cannot drift apart.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from tasks.models import EscalationKind, EscalationRequest


WORKER_TASK_RESULT_NAME = "worker_task_result"

COMPLETED = "completed"
BLOCKED = "blocked"


class InvalidWorkerResult(ValueError):
    """A Task Worker attempt did not produce a valid canonical result."""


_ESCALATION_FIELD: dict[EscalationKind, str] = {
    EscalationKind.DOMAIN_REQUIRED: "requested_domain",
    EscalationKind.TOOL_REQUIRED: "requested_tool",
    EscalationKind.SKILL_LIMIT_EXCEEDED: "requested_skill_limit",
}


def _validate_escalation(
    escalation: EscalationRequest,
) -> None:
    """
    Validate the shape of one canonical escalation.

    This is Worker-result validation only. Whether the requested
    authority can actually be granted is Supervisor validation and is
    deliberately not duplicated here.
    """

    if not isinstance(escalation.kind, EscalationKind):
        raise ValueError(
            "Escalation 'kind' is not a canonical EscalationKind."
        )

    if (
        not isinstance(escalation.reason, str)
        or not escalation.reason.strip()
    ):
        raise ValueError(
            "Escalation requires a non-empty 'reason'."
        )

    required_field = _ESCALATION_FIELD[escalation.kind]

    provided = {
        "requested_domain": escalation.requested_domain,
        "requested_tool": escalation.requested_tool,
        "requested_skill_limit": (
            escalation.requested_skill_limit
        ),
    }

    for field, value in provided.items():
        if field == required_field:
            if value is None or (
                isinstance(value, str)
                and not value.strip()
            ):
                raise ValueError(
                    f"{escalation.kind.value} requires "
                    f"'{field}'."
                )
            continue

        if value is not None:
            raise ValueError(
                f"{escalation.kind.value} must not carry "
                f"'{field}'."
            )

    if (
        escalation.kind
        is EscalationKind.SKILL_LIMIT_EXCEEDED
    ):
        limit = escalation.requested_skill_limit

        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
        ):
            raise ValueError(
                "skill_limit_exceeded requires a positive "
                "integer 'requested_skill_limit'."
            )


class WorkerTaskResult(BaseModel):
    """
    Canonical structured result of one Task Worker attempt.

    COMPLETED carries a result and no escalation.

    BLOCKED carries the accumulated work and exactly one canonical
    EscalationRequest. Contradictory lifecycle states are rejected.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "blocked"]

    result: dict[str, Any] | None = None

    progress: str = ""
    findings: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    remaining_work: str = ""

    escalation: EscalationRequest | None = None

    @model_validator(mode="after")
    def _validate_lifecycle(
        self,
    ) -> "WorkerTaskResult":

        if self.status == COMPLETED:
            if self.escalation is not None:
                raise ValueError(
                    "A completed Worker result must not carry "
                    "an escalation."
                )

            return self

        if self.escalation is None:
            raise ValueError(
                "A blocked Worker result requires an escalation."
            )

        _validate_escalation(self.escalation)

        return self

    @property
    def is_blocked(
        self,
    ) -> bool:
        return self.status == BLOCKED

    @classmethod
    def from_payload(
        cls,
        payload: Any,
    ) -> "WorkerTaskResult":
        """
        Validate one model-facing payload.

        Accepts an already-canonical result, a JSON string, or a dict.
        Anything else is an explicit InvalidWorkerResult; lifecycle is
        never inferred from prose.
        """

        if isinstance(payload, cls):
            return payload

        if isinstance(payload, str):
            text = payload.strip()

            if not text:
                raise InvalidWorkerResult(
                    "Worker result is empty."
                )

            try:
                decoded = json.loads(text)

            except json.JSONDecodeError as exc:
                raise InvalidWorkerResult(
                    "Worker result is not valid JSON: "
                    f"{exc.msg}."
                ) from exc

        else:
            decoded = payload

        if not isinstance(decoded, dict):
            raise InvalidWorkerResult(
                "Worker result must be a JSON object."
            )

        try:
            return cls.model_validate(decoded)

        except ValidationError as exc:
            raise InvalidWorkerResult(
                _format_validation_error(exc)
            ) from exc


def _format_validation_error(
    exc: ValidationError,
) -> str:
    """Render bounded, structural validation problems."""

    problems: list[str] = []

    for error in exc.errors():
        location = ".".join(
            str(part) for part in error["loc"]
        ) or "<result>"

        problems.append(
            f"{location}: {error['msg']}"
        )

    return "Invalid Worker result: " + "; ".join(
        problems
    )


def _finalization_schema(
    schema: Any,
) -> Any:
    """
    Normalize a derived JSON schema for structured finalization.

    Object fields must be explicit: every property is required and
    unknown properties are rejected. The free-form ``result`` payload
    stays an open object.
    """

    if isinstance(schema, list):
        return [
            _finalization_schema(item)
            for item in schema
        ]

    if not isinstance(schema, dict):
        return schema

    normalized = {
        key: _finalization_schema(value)
        for key, value in schema.items()
    }

    normalized.pop("default", None)

    properties = normalized.get("properties")

    if isinstance(properties, dict):
        normalized["required"] = list(properties)
        normalized["additionalProperties"] = False

    return normalized


WORKER_TASK_RESULT_SCHEMA: dict[str, Any] = (
    _finalization_schema(
        WorkerTaskResult.model_json_schema()
    )
)


WORKER_TASK_RESULT_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": WORKER_TASK_RESULT_NAME,
        "strict": False,
        "schema": WORKER_TASK_RESULT_SCHEMA,
    },
}


class WorkerIterationExhausted(Exception):
    """
    The attempt ended because its iteration budget ran out.

    This is an execution-budget condition, not an authority
    escalation: no canonical EscalationKind describes it, and the
    finalizer never decides it. ``result`` carries only the partial
    state a bounded packaging step could extract, so the runtime can
    preserve useful work while still recording the attempt as FAILED.

    Workers without a finalization contract raise RuntimeError
    instead, preserving their existing behavior.
    """

    def __init__(
        self,
        result: "WorkerTaskResult | None" = None,
        message: str = (
            "Worker iteration budget exhausted before the Task "
            "completed."
        ),
    ) -> None:
        super().__init__(message)
        self.result = result
