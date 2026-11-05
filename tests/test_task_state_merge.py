"""Runtime-owned TaskState merge semantics.

TaskState persistence must not depend on a replacement Worker echoing
prior state back. These tests pin the deterministic merge policy.
"""

from __future__ import annotations

from typing import Any

from supervisor.escalation import (
    PROGRESS_MAX_CHARS,
    apply_outcome,
)
from tasks.models import (
    EscalationKind,
    EscalationRequest,
    TaskState,
)
from tasks.outcome import TaskOutcome
from tasks.task import TaskStatus


def make_state(
    **kwargs: Any,
) -> TaskState:
    base: dict[str, Any] = {
        "task_id": "task_merge",
        "instruction": "Do the work.",
    }

    base.update(kwargs)

    return TaskState(**base)


def blocked_outcome(
    **kwargs: Any,
) -> TaskOutcome:
    base: dict[str, Any] = {
        "request_id": "req_merge",
        "task_id": "task_merge",
        "status": TaskStatus.BLOCKED,
        "escalation": EscalationRequest(
            kind=EscalationKind.DOMAIN_REQUIRED,
            reason="Need research access.",
            requested_domain="research",
        ),
    }

    base.update(kwargs)

    return TaskOutcome(**base)


def test_findings_survive_append_and_dedupe() -> None:
    state = make_state(findings=["A", "B"])

    apply_outcome(
        state,
        blocked_outcome(findings=("B", "C")),
    )

    assert state.findings == ["A", "B", "C"]


def test_empty_findings_do_not_erase_accumulated_findings() -> None:
    state = make_state(findings=["A", "B"])

    apply_outcome(
        state,
        blocked_outcome(findings=()),
    )

    assert state.findings == ["A", "B"]


def test_artifacts_survive_append_and_dedupe() -> None:
    state = make_state(artifacts=["report.md"])

    apply_outcome(
        state,
        blocked_outcome(
            artifacts=("report.md", "graph.png")
        ),
    )

    assert state.artifacts == [
        "report.md",
        "graph.png",
    ]


def test_empty_progress_does_not_erase_existing_progress() -> None:
    state = make_state(progress="Mapped the parser.")

    apply_outcome(
        state,
        blocked_outcome(progress=""),
    )

    assert state.progress == "Mapped the parser."


def test_new_progress_preserves_accumulated_meaning() -> None:
    state = make_state(progress="Mapped the parser.")

    apply_outcome(
        state,
        blocked_outcome(progress="Found the tokenizer bug."),
    )

    assert "Mapped the parser." in state.progress
    assert "Found the tokenizer bug." in state.progress
    assert len(state.progress) <= PROGRESS_MAX_CHARS


def test_identical_progress_is_unchanged() -> None:
    state = make_state(progress="Mapped the parser.")

    apply_outcome(
        state,
        blocked_outcome(progress="Mapped the parser."),
    )

    assert state.progress == "Mapped the parser."


def test_progress_fills_when_previously_empty() -> None:
    state = make_state(progress="")

    apply_outcome(
        state,
        blocked_outcome(progress="Started inspecting."),
    )

    assert state.progress == "Started inspecting."


def test_blocked_empty_remaining_work_keeps_existing_work() -> None:
    state = make_state(remaining_work="Run the tests.")

    apply_outcome(
        state,
        blocked_outcome(remaining_work=""),
    )

    assert state.remaining_work == "Run the tests."


def test_blocked_remaining_work_is_replaced_by_new_value() -> None:
    state = make_state(remaining_work="Run the tests.")

    apply_outcome(
        state,
        blocked_outcome(
            remaining_work="Verify the fix."
        ),
    )

    assert state.remaining_work == "Verify the fix."


def test_completed_outcome_clears_remaining_work() -> None:
    state = make_state(remaining_work="Run the tests.")

    apply_outcome(
        state,
        TaskOutcome(
            request_id="req_merge",
            task_id="task_merge",
            status=TaskStatus.COMPLETED,
            result={"content": "done"},
            remaining_work="",
        ),
    )

    assert state.remaining_work == ""


def test_control_state_is_never_taken_from_the_outcome() -> None:
    state = make_state(
        attempt=2,
        authorized_domains={"coding"},
        skill_limit=5,
        loaded_skills=["coding.codebase-inspection"],
    )

    apply_outcome(
        state,
        blocked_outcome(
            progress="More work.",
            findings=("F",),
        ),
    )

    assert state.attempt == 2
    assert state.authorized_domains == {"coding"}
    assert state.skill_limit == 5
    assert state.loaded_skills == [
        "coding.codebase-inspection"
    ]


def test_three_consecutive_blocked_attempts_accumulate() -> None:
    state = make_state()

    apply_outcome(
        state,
        blocked_outcome(
            progress="Attempt one.",
            findings=("A",),
            artifacts=("one.txt",),
            remaining_work="Step two.",
        ),
    )

    apply_outcome(
        state,
        blocked_outcome(
            progress="Attempt two.",
            findings=("B",),
            artifacts=("two.txt",),
            remaining_work="Step three.",
        ),
    )

    apply_outcome(
        state,
        blocked_outcome(
            progress="",
            findings=("A", "C"),
            artifacts=(),
            remaining_work="",
        ),
    )

    assert "Attempt one." in state.progress
    assert "Attempt two." in state.progress
    assert state.findings == ["A", "B", "C"]
    assert state.artifacts == ["one.txt", "two.txt"]
    assert state.remaining_work == "Step three."
