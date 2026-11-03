"""Data models for the Second Brain runtime."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RelatedSkill:
    """
    A related skill exposed to the model for methodology navigation.

    Relationships are advisory. They do not grant skill, domain,
    or tool authority.
    """

    skill_id: str
    description: str


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    """
    Runtime-facing metadata parsed from a skill's YAML frontmatter.

    Used internally for validation and projection. This object is not
    intended to be injected directly into model context.
    """

    skill_id: str
    name: str
    domain: str
    description: str
    related: tuple[RelatedSkill, ...]


@dataclass(frozen=True, slots=True)
class SkillProjection:
    """
    Model-facing representation returned when a skill is loaded.

    Contains only information useful to the model. Runtime bookkeeping,
    authorization information, provenance, licensing, and Obsidian-only
    content are intentionally excluded.
    """

    name: str
    description: str
    content: str
    related: tuple[RelatedSkill, ...]