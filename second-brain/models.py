"""Data models for the Second Brain runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SecondBrainErrorCode(str, Enum):
    """Classification for Second Brain runtime failures."""

    REGISTRY_NOT_FOUND = "registry_not_found"
    DOMAIN_NOT_FOUND = "domain_not_found"
    SKILL_NOT_FOUND = "skill_not_found"
    SKILL_PARSE_ERROR = "skill_parse_error"
    SKILL_VALIDATION_ERROR = "skill_validation_error"
    UNAUTHORIZED_SKILL = "unauthorized_skill"


@dataclass(frozen=True, slots=True)
class SecondBrainError:
    """Structured error returned by the Second Brain runtime."""

    code: SecondBrainErrorCode
    message: str


@dataclass(frozen=True, slots=True)
class RelatedSkill:
    """Related methodology available for navigation."""

    skill_id: str
    description: str


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    """Runtime-facing metadata parsed from skill YAML."""

    skill_id: str
    name: str
    domain: str
    description: str
    related: tuple[RelatedSkill, ...]


@dataclass(frozen=True, slots=True)
class SkillProjection:
    """Model-facing projection of a loaded skill."""

    name: str
    description: str
    content: str
    related: tuple[RelatedSkill, ...]