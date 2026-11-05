"""Deterministic Supervisor-side escalation mechanics.

This module owns the parts of escalation that must not depend on a
model: validation, attempt limits, authority mutation, skill-limit
policy, and decision application.

The Supervisor makes the semantic decision (approve / deny /
terminate). Everything else here is deterministic, so the same
outcome always produces the same control-state change.

Tool authority is derived, never held directly:

    TaskState.authorized_domains
            +
    TrustedDomainGrants
            ↓
    allowed_tools

A TOOL_REQUIRED escalation therefore resolves a tool to the trusted
domain(s) able to grant it, and grants at most one domain. Nothing in
TaskState stores an allowed-tool list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from supervisor.models import (
    EscalationDecision,
    EscalationDecisionKind,
)
from tasks.models import (
    EscalationKind,
    EscalationRequest,
    TaskState,
)
from tasks.outcome import TaskOutcome
from tasks.task import Task, TaskStatus
from tool_grants.models import ToolAuthorityResolver


#: An attempt beyond this cannot start.
MAX_ATTEMPTS = 4

#: Approved skill-limit escalation grants this many extra skills.
SKILL_LIMIT_INCREMENT = 2

#: Hard ceiling for a Task's skill limit.
MAX_SKILL_LIMIT = 11

#: Bound for accumulated progress text across attempts.
PROGRESS_MAX_CHARS = 4000


class EscalationValidationError(ValueError):
    """
    A Worker's escalation cannot be honoured as written.

    Validation is deterministic: an invalid escalation is not handed
    to the Supervisor for a decision.
    """


@dataclass(frozen=True, slots=True)
class ResolvedEscalation:
    """
    A validated escalation, ready for a Supervisor decision.

    ``candidate_domains`` lists the trusted domains able to satisfy a
    TOOL_REQUIRED request. ``grantable`` is False when no authority
    expansion can honour the request, whatever the decision.
    """

    kind: EscalationKind
    grantable: bool = True
    candidate_domains: tuple[str, ...] = ()
    requested_tool: str | None = None


@dataclass(frozen=True, slots=True)
class EscalationResolution:
    """
    The control-state change produced by one decision.
    """

    kind: EscalationKind
    restart: bool

    domains_before: tuple[str, ...]
    domains_after: tuple[str, ...]

    skill_limit_before: int
    skill_limit_after: int

    attempt_before: int
    attempt_after: int

    granted_domain: str | None = None


def apply_outcome(
    state: TaskState,
    outcome: TaskOutcome,
) -> None:
    """
    Persist a Worker's accumulated work onto its TaskState.

    Persistence is runtime-owned and conservative: accumulated work
    survives a replacement Worker, so TaskState never depends on the
    next model echoing prior state back. Control state — attempt,
    authorized domains, skill limit, loaded skills — is never taken
    from Worker output.
    """

    state.progress = _merge_progress(
        state.progress,
        outcome.progress,
    )
    state.findings = _merge_unique(
        state.findings,
        outcome.findings,
    )
    state.artifacts = _merge_unique(
        state.artifacts,
        outcome.artifacts,
    )
    state.remaining_work = _merge_remaining_work(
        existing=state.remaining_work,
        incoming=outcome.remaining_work,
        completed=(outcome.status is TaskStatus.COMPLETED),
    )
    state.escalation = outcome.escalation


def _merge_unique(
    existing: list[str],
    incoming: tuple[str, ...],
) -> list[str]:
    """
    Append new unique entries, preserving existing order.

    An empty incoming collection never erases accumulated work.
    """

    merged = list(existing)
    seen = set(existing)

    for item in incoming:
        if item in seen:
            continue

        merged.append(item)
        seen.add(item)

    return merged


def _merge_progress(
    existing: str,
    incoming: str,
) -> str:
    """
    Merge attempt progress deterministically.

    Empty values never erase meaningful existing progress. Two
    different non-empty values are both preserved, bounded by
    PROGRESS_MAX_CHARS; no summarization model is involved.
    """

    if not incoming.strip():
        return existing

    if not existing.strip():
        return incoming

    if existing.strip() == incoming.strip():
        return existing

    combined = f"{existing.rstrip()}\n\n{incoming.strip()}"

    if len(combined) <= PROGRESS_MAX_CHARS:
        return combined

    return combined[-PROGRESS_MAX_CHARS:]


def _merge_remaining_work(
    *,
    existing: str,
    incoming: str,
    completed: bool,
) -> str:
    """
    Merge remaining work.

    Remaining work describes the current frontier rather than
    history, so a newer non-empty value replaces the old one. A
    COMPLETED outcome may legitimately clear it; a BLOCKED outcome
    with empty remaining work must not erase meaningful existing work.
    """

    if completed:
        return incoming

    if incoming.strip():
        return incoming

    return existing


def validate_escalation(
    state: TaskState,
    escalation: EscalationRequest | None,
    *,
    domain_exists: Callable[[str], bool],
    tool_authority: ToolAuthorityResolver,
) -> ResolvedEscalation:
    """
    Deterministically validate one escalation request.

    Raises EscalationValidationError when the request is malformed,
    redundant, or refers to authority that does not exist.
    """

    if escalation is None:
        raise EscalationValidationError(
            "A BLOCKED Task must carry an EscalationRequest."
        )

    try:
        kind = EscalationKind(escalation.kind)

    except ValueError as exc:
        raise EscalationValidationError(
            f"Unknown escalation kind {escalation.kind!r}."
        ) from exc

    if kind is EscalationKind.DOMAIN_REQUIRED:
        domain = _require_identifier(
            escalation.requested_domain,
            "requested_domain",
        )

        if domain in state.authorized_domains:
            raise EscalationValidationError(
                f"Domain {domain!r} is already authorized for "
                "this Task."
            )

        if not domain_exists(domain):
            raise EscalationValidationError(
                f"Unknown domain {domain!r}."
            )

        return ResolvedEscalation(
            kind=kind,
            grantable=True,
            candidate_domains=(domain,),
        )

    if kind is EscalationKind.TOOL_REQUIRED:
        tool = _require_identifier(
            escalation.requested_tool,
            "requested_tool",
        )

        # The tool may already be reachable through the Task's
        # current domains; asking for it again is not an escalation.
        for domain in state.authorized_domains:
            if tool in tool_authority.tools_for_domain(
                domain
            ):
                raise EscalationValidationError(
                    f"Tool {tool!r} is already granted by "
                    f"authorized domain {domain!r}."
                )

        candidates = tuple(
            sorted(
                tool_authority.domains_for_tool(tool)
            )
        )

        # No trusted grant provides the tool, so no decision can
        # manufacture the authority.
        return ResolvedEscalation(
            kind=kind,
            grantable=bool(candidates),
            candidate_domains=candidates,
            requested_tool=tool,
        )

    if kind is EscalationKind.SKILL_LIMIT_EXCEEDED:
        requested = escalation.requested_skill_limit

        if (
            not isinstance(requested, int)
            or isinstance(requested, bool)
        ):
            raise EscalationValidationError(
                "SKILL_LIMIT_EXCEEDED requires an integer "
                "'requested_skill_limit'."
            )

        if requested <= state.skill_limit:
            raise EscalationValidationError(
                "SKILL_LIMIT_EXCEEDED requires a limit greater "
                f"than the current limit ({state.skill_limit})."
            )

        # The requested number is informational: the runtime decides
        # the grant. Nothing is left to grant at the ceiling.
        return ResolvedEscalation(
            kind=kind,
            grantable=state.skill_limit < MAX_SKILL_LIMIT,
        )

    raise EscalationValidationError(
        f"Unsupported escalation kind {kind!r}."
    )


def apply_decision(
    *,
    task: Task,
    state: TaskState,
    decision: EscalationDecision,
    resolved: ResolvedEscalation,
    select_domain: (
        Callable[[tuple[str, ...], TaskState], str] | None
    ) = None,
) -> EscalationResolution:
    """
    Apply the Supervisor's decision to Task and TaskState.

    A restart keeps the same Task and TaskState and consumes an
    attempt. Termination preserves the accumulated work so the Request
    can still synthesize from it.
    """

    domains_before = tuple(
        sorted(state.authorized_domains)
    )
    skill_limit_before = state.skill_limit
    attempt_before = state.attempt

    restart = (
        decision.decision
        is not EscalationDecisionKind.TERMINATE
    )

    # A restart must consume an attempt; attempt MAX_ATTEMPTS is the
    # last attempt that may run.
    if restart and attempt_before >= MAX_ATTEMPTS:
        restart = False

    # An approval of something that cannot be granted is not a
    # restart: authority is never faked.
    if (
        restart
        and decision.decision
        is EscalationDecisionKind.APPROVE
        and not resolved.grantable
    ):
        restart = False

    granted_domain: str | None = None

    if not restart:
        task.status = TaskStatus.FAILED

        return EscalationResolution(
            kind=resolved.kind,
            restart=False,
            domains_before=domains_before,
            domains_after=domains_before,
            skill_limit_before=skill_limit_before,
            skill_limit_after=skill_limit_before,
            attempt_before=attempt_before,
            attempt_after=attempt_before,
        )

    if decision.decision is EscalationDecisionKind.APPROVE:
        if resolved.kind is EscalationKind.DOMAIN_REQUIRED:
            granted_domain = resolved.candidate_domains[0]

        elif resolved.kind is EscalationKind.TOOL_REQUIRED:
            candidates = resolved.candidate_domains

            if not candidates:
                raise ValueError(
                    "TOOL_REQUIRED escalation has no granting "
                    "domain."
                )

            if select_domain is None:
                granted_domain = candidates[0]
            else:
                granted_domain = select_domain(
                    candidates,
                    state,
                )

            if granted_domain not in candidates:
                raise ValueError(
                    f"Domain {granted_domain!r} does not grant the "
                    "requested tool."
                )

        elif (
            resolved.kind
            is EscalationKind.SKILL_LIMIT_EXCEEDED
        ):
            # Runtime owns the amount. The Worker's requested number
            # is never assigned directly.
            state.skill_limit = min(
                skill_limit_before + SKILL_LIMIT_INCREMENT,
                MAX_SKILL_LIMIT,
            )

        if granted_domain is not None:
            state.authorized_domains.add(
                granted_domain
            )

    state.attempt = attempt_before + 1
    state.escalation = None
    task.status = TaskStatus.QUEUED

    return EscalationResolution(
        kind=resolved.kind,
        restart=True,
        domains_before=domains_before,
        domains_after=tuple(
            sorted(state.authorized_domains)
        ),
        skill_limit_before=skill_limit_before,
        skill_limit_after=state.skill_limit,
        attempt_before=attempt_before,
        attempt_after=state.attempt,
        granted_domain=granted_domain,
    )


def _require_identifier(
    value: str | None,
    field: str,
) -> str:
    """
    Normalize a required escalation identifier.
    """

    if not isinstance(value, str):
        raise EscalationValidationError(
            f"This escalation requires a non-empty '{field}'."
        )

    value = value.strip()

    if not value:
        raise EscalationValidationError(
            f"This escalation requires a non-empty '{field}'."
        )

    return value
