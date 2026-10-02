"""Local skill registry for KernelAI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


SKILLS_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Skill:
    """
    Definition of a skill available to KernelAI.
    """

    name: str
    description: str
    path: Path
    allowed_tools: set[str]

    def load(self) -> str:
        """
        Load and return the full skill instructions.
        """
        return self.path.read_text(
            encoding="utf-8"
        )


SKILLS: dict[str, Skill] = {
    "text2sql": Skill(
        name="text2sql",
        description=(
            "Answer questions using the connected PostgreSQL "
            "database through schema discovery and read-only SQL."
        ),
        path=SKILLS_DIR / "text2sql.md",
        allowed_tools={
            "sql_executor",
        },
    ),

    "web_search": Skill(
        name="web_search",
        description=(
            "Research current or external information using "
            "web search."
        ),
        path=SKILLS_DIR / "web_search.md",
        allowed_tools={
            "web_search",
        },
    ),
}


def get_skill(
    name: str,
) -> Skill:
    """
    Return a skill definition by name.
    """

    try:
        return SKILLS[name]

    except KeyError as exc:
        raise ValueError(
            f"Unknown skill {name!r}."
        ) from exc


def list_skills() -> list[dict[str, object]]:
    """
    Return lightweight descriptions of all delegatable skills.

    Full skill prompts are intentionally excluded so the
    orchestrator does not need to load every skill into context.
    """

    return [
        {
            "name": skill.name,
            "description": skill.description,
            "allowed_tools": sorted(
                skill.allowed_tools
            ),
        }
        for skill in SKILLS.values()
    ]