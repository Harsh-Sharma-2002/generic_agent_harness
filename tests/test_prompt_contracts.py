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
        "search rounds",
        "two rounds is the normal maximum",
        "do not start a third round",
        "successor products",
        "supervisor's job through separate tasks",
    ):
        assert phrase in text

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
