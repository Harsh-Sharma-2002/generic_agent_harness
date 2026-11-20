"""Content contracts for the KernelAI prompt and research skill files."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

EDITED_PROMPT_FILES = (
    "supervisor/supervisor.md",
    "tasks/task_worker.md",
    "vault/skills/research/web-search/web-search.md",
)


def _normalized(relative_path: str) -> str:
    """Read a prompt by its runtime path and normalize line wrapping."""

    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")

    return " ".join(text.lower().split())


def test_supervisor_prompt_contract() -> None:
    text = _normalized("supervisor/supervisor.md")

    for phrase in (
        "named aspects of one subject",
        "each named thing is an independent objective",
        "decompose by independent work, not by domain",
        "before waiting for task outcomes",
        "do not give one task several independent objectives",
    ):
        assert phrase in text


def test_web_search_prompt_contract() -> None:
    text = _normalized(
        "vault/skills/research/web-search/web-search.md"
    )

    for phrase in (
        "absolute maximum of two `web_search` calls",
        "hard execution limit",
        "search 1: high-yield retrieval",
        "search 2: one named gap",
        "after search 2, searching is finished",
        "successor products",
        "supervisor's responsibility",
    ):
        assert phrase in text

    for obsolete in (
        "search rounds",
        "two rounds is the normal maximum",
        "exceed two rounds",
    ):
        assert obsolete not in text

    assert "there is no search quota" not in text


def test_edited_prompts_avoid_model_specific_examples() -> None:
    for relative_path in EDITED_PROMPT_FILES:
        text = _normalized(relative_path)

        for term in ("blackwell", "nvidia", "rubin"):
            assert term not in text, f"{term!r} found in {relative_path}"


def test_task_worker_prompt_contract() -> None:
    text = _normalized("tasks/task_worker.md")

    for phrase in (
        "call `get_skill` immediately",
        "proportional to the task",
    ):
        assert phrase in text


def test_web_search_budget_exhaustion_is_not_an_escalation() -> None:
    text = _normalized(
        "vault/skills/research/web-search/web-search.md"
    )

    for phrase in (
        "tool-budget exhaustion is not a blocking condition",
        "never grounds for capability escalation",
        "complete the assigned task using the best available evidence",
        "research tasks do not require perfect information",
        "state the missing or unverified detail",
        "do not request additional `web_search` capability",
        "not a signal that authority is missing",
    ):
        assert phrase in text

    # No surviving language offering retrieval beyond the budget.
    for obsolete in (
        "exceptional extra search",
        "third search",
        "ask the supervisor for more searches",
        "you may request additional searches",
    ):
        assert obsolete not in text


def test_task_worker_budget_is_not_missing_capability() -> None:
    text = _normalized("tasks/task_worker.md")

    for phrase in (
        "missing capability",
        "exhausted budget for an existing capability",
        "resource-budget exhaustion does not mean missing capability",
        "do not request that same capability through escalation",
        "work and evidence already available",
        "wanting more of an authorized resource is not the same as lacking it",
        "is not an authority boundary",
    ):
        assert phrase in text


def test_supervisor_does_not_recreate_work_to_bypass_budgets() -> None:
    text = _normalized("supervisor/supervisor.md")

    for phrase in (
        "a failed or blocked outcome does not automatically justify "
        "delegating a replacement task",
        "do not recreate equivalent work merely to bypass a worker "
        "methodology or execution budget",
        "not additional capacity for work already attempted",
    ):
        assert phrase in text
