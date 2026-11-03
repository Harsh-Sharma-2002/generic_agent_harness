"""Tests for the Second Brain runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from second_brain.models import (
    LoadedSkill,
    SecondBrainError,
    SecondBrainErrorCode,
)
from second_brain.runtime import SecondBrainRuntime


REGISTRY = """\
# Skill Registry

## Domains

### coding
Software-development methodology.

### research
External-information retrieval.
"""


CODING_DOMAIN = """\
# Coding

## Skills

### coding.systematic-debugging

Use when something fails and the cause is not understood.

### coding.test-driven-development

Use when expected behavior is known and implementation should proceed
under test discipline.
"""


RESEARCH_DOMAIN = """\
# Research

## Skills

### research.web-search

Use when current external information is required.
"""


SYSTEMATIC_DEBUGGING = """\
---
name: systematic-debugging
description: "Diagnose failures systematically before proposing fixes."
id: coding.systematic-debugging
related:
  - skill: coding.test-driven-development
    description: "Useful once the root cause is known and the fix should be regression-tested."
domain: coding
version: 1.0.0
author: Test Author
license: MIT
---

# Systematic Debugging

Diagnose the failure before attempting a fix.

## Workflow

1. Reproduce the failure.
2. Gather evidence.
3. Identify the root cause.
4. Verify the diagnosis.

## Related Skills

- [[skills/coding/test-driven-development/test-driven-development|coding.test-driven-development]]
"""


TEST_DRIVEN_DEVELOPMENT = """\
---
name: test-driven-development
description: "Implement behavior by writing the failing test first."
id: coding.test-driven-development
related:
  - skill: coding.systematic-debugging
    description: "Useful when the cause of a failure is not yet understood."
domain: coding
---

# Test-Driven Development

Write the failing test before implementation.
"""


WEB_SEARCH = """\
---
name: web-search
description: "Retrieve current external information."
id: research.web-search
related: []
domain: research
---

# Web Search

Search external sources and synthesize the findings.
"""


def write_skill(
    vault: Path,
    domain: str,
    skill_name: str,
    content: str,
) -> Path:
    """Create one canonical skill file inside the temporary vault."""

    skill_dir = vault / "skills" / domain / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)

    skill_path = skill_dir / f"{skill_name}.md"
    skill_path.write_text(content, encoding="utf-8")

    return skill_path


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Create a minimal valid Second Brain vault."""

    skills = tmp_path / "skills"

    (skills / "coding").mkdir(parents=True)
    (skills / "research").mkdir(parents=True)

    (skills / "REGISTRY.md").write_text(
        REGISTRY,
        encoding="utf-8",
    )

    (skills / "coding" / "DOMAIN.md").write_text(
        CODING_DOMAIN,
        encoding="utf-8",
    )

    (skills / "research" / "DOMAIN.md").write_text(
        RESEARCH_DOMAIN,
        encoding="utf-8",
    )

    write_skill(
        tmp_path,
        "coding",
        "systematic-debugging",
        SYSTEMATIC_DEBUGGING,
    )

    write_skill(
        tmp_path,
        "coding",
        "test-driven-development",
        TEST_DRIVEN_DEVELOPMENT,
    )

    write_skill(
        tmp_path,
        "research",
        "web-search",
        WEB_SEARCH,
    )

    return tmp_path


@pytest.fixture
def runtime(vault: Path) -> SecondBrainRuntime:
    """Return a runtime backed by the temporary vault."""

    return SecondBrainRuntime(vault)


def test_get_registry(runtime: SecondBrainRuntime) -> None:
    result = runtime.get_registry()

    assert isinstance(result, str)
    assert "# Skill Registry" in result
    assert "coding" in result
    assert "research" in result


def test_missing_registry_returns_error(tmp_path: Path) -> None:
    runtime = SecondBrainRuntime(tmp_path)

    result = runtime.get_registry()

    assert isinstance(result, SecondBrainError)
    assert result.code == SecondBrainErrorCode.REGISTRY_NOT_FOUND


def test_get_domain(runtime: SecondBrainRuntime) -> None:
    result = runtime.get_domain("coding")

    assert isinstance(result, str)
    assert "# Coding" in result
    assert "coding.systematic-debugging" in result


def test_missing_domain_returns_error(
    runtime: SecondBrainRuntime,
) -> None:
    result = runtime.get_domain("teaching")

    assert isinstance(result, SecondBrainError)
    assert result.code == SecondBrainErrorCode.DOMAIN_NOT_FOUND


@pytest.mark.parametrize(
    "domain",
    [
        "../coding",
        "../../etc",
        "Coding",
        "coding/domain",
        "",
    ],
)
def test_invalid_domain_segment_is_rejected(
    runtime: SecondBrainRuntime,
    domain: str,
) -> None:
    result = runtime.get_domain(domain)

    assert isinstance(result, SecondBrainError)
    assert result.code == SecondBrainErrorCode.DOMAIN_NOT_FOUND


def test_get_skill_returns_loaded_skill(
    runtime: SecondBrainRuntime,
) -> None:
    result = runtime.get_skill(
        "coding.systematic-debugging",
        {"coding"},
    )

    assert isinstance(result, LoadedSkill)

    assert result.metadata.skill_id == "coding.systematic-debugging"
    assert result.metadata.name == "systematic-debugging"
    assert result.metadata.domain == "coding"
    assert (
        result.metadata.description
        == "Diagnose failures systematically before proposing fixes."
    )

    assert result.projection.name == "systematic-debugging"
    assert (
        result.projection.description
        == "Diagnose failures systematically before proposing fixes."
    )

    assert "Diagnose the failure before attempting a fix." in (
        result.projection.content
    )


def test_skill_related_metadata_is_parsed(
    runtime: SecondBrainRuntime,
) -> None:
    result = runtime.get_skill(
        "coding.systematic-debugging",
        {"coding"},
    )

    assert isinstance(result, LoadedSkill)
    assert len(result.metadata.related) == 1

    related = result.metadata.related[0]

    assert related.skill_id == "coding.test-driven-development"
    assert (
        related.description
        == (
            "Useful once the root cause is known and the fix "
            "should be regression-tested."
        )
    )

    assert result.projection.related == result.metadata.related


def test_obsidian_related_section_is_removed_from_projection(
    runtime: SecondBrainRuntime,
) -> None:
    result = runtime.get_skill(
        "coding.systematic-debugging",
        {"coding"},
    )

    assert isinstance(result, LoadedSkill)

    assert "## Related Skills" not in result.projection.content
    assert "[[skills/coding/" not in result.projection.content

    # Normal methodology content must remain.
    assert "## Workflow" in result.projection.content
    assert "Identify the root cause." in result.projection.content


def test_skill_from_authorized_domain_loads(
    runtime: SecondBrainRuntime,
) -> None:
    result = runtime.get_skill(
        "research.web-search",
        {"research"},
    )

    assert isinstance(result, LoadedSkill)
    assert result.metadata.domain == "research"


def test_skill_from_unauthorized_domain_is_rejected(
    runtime: SecondBrainRuntime,
) -> None:
    result = runtime.get_skill(
        "research.web-search",
        {"coding"},
    )

    assert isinstance(result, SecondBrainError)
    assert result.code == SecondBrainErrorCode.UNAUTHORIZED_SKILL


def test_multiple_authorized_domains_are_supported(
    runtime: SecondBrainRuntime,
) -> None:
    result = runtime.get_skill(
        "research.web-search",
        {"coding", "research"},
    )

    assert isinstance(result, LoadedSkill)
    assert result.metadata.skill_id == "research.web-search"


def test_nonexistent_skill_returns_error(
    runtime: SecondBrainRuntime,
) -> None:
    result = runtime.get_skill(
        "coding.does-not-exist",
        {"coding"},
    )

    assert isinstance(result, SecondBrainError)
    assert result.code == SecondBrainErrorCode.SKILL_NOT_FOUND


@pytest.mark.parametrize(
    "skill_id",
    [
        "",
        "systematic-debugging",
        "coding.",
        ".systematic-debugging",
        "coding.systematic.debugging",
        "Coding.systematic-debugging",
        "../coding.systematic-debugging",
        "coding.systematic/debugging",
    ],
)
def test_invalid_skill_id_is_rejected(
    runtime: SecondBrainRuntime,
    skill_id: str,
) -> None:
    result = runtime.get_skill(
        skill_id,
        {"coding"},
    )

    assert isinstance(result, SecondBrainError)
    assert result.code == SecondBrainErrorCode.SKILL_VALIDATION_ERROR


def test_missing_yaml_frontmatter_returns_parse_error(
    vault: Path,
) -> None:
    write_skill(
        vault,
        "coding",
        "broken-skill",
        "# Broken Skill\n\nNo frontmatter.",
    )

    runtime = SecondBrainRuntime(vault)

    result = runtime.get_skill(
        "coding.broken-skill",
        {"coding"},
    )

    assert isinstance(result, SecondBrainError)
    assert result.code == SecondBrainErrorCode.SKILL_PARSE_ERROR


def test_invalid_yaml_returns_parse_error(
    vault: Path,
) -> None:
    write_skill(
        vault,
        "coding",
        "broken-skill",
        """\
---
name: broken-skill
description: [invalid
id: coding.broken-skill
domain: coding
related: []
---

# Broken
""",
    )

    runtime = SecondBrainRuntime(vault)

    result = runtime.get_skill(
        "coding.broken-skill",
        {"coding"},
    )

    assert isinstance(result, SecondBrainError)
    assert result.code == SecondBrainErrorCode.SKILL_PARSE_ERROR


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("name", "wrong-name"),
        ("id", "coding.wrong-id"),
        ("domain", "research"),
    ],
)
def test_skill_identity_mismatch_is_rejected(
    vault: Path,
    field: str,
    replacement: str,
) -> None:
    content = SYSTEMATIC_DEBUGGING

    if field == "name":
        content = content.replace(
            "name: systematic-debugging",
            f"name: {replacement}",
            1,
        )

    elif field == "id":
        content = content.replace(
            "id: coding.systematic-debugging",
            f"id: {replacement}",
            1,
        )

    elif field == "domain":
        content = content.replace(
            "domain: coding",
            f"domain: {replacement}",
            1,
        )

    write_skill(
        vault,
        "coding",
        "systematic-debugging",
        content,
    )

    runtime = SecondBrainRuntime(vault)

    result = runtime.get_skill(
        "coding.systematic-debugging",
        {"coding"},
    )

    assert isinstance(result, SecondBrainError)
    assert result.code == SecondBrainErrorCode.SKILL_VALIDATION_ERROR


@pytest.mark.parametrize(
    "missing_field",
    [
        "name",
        "description",
        "id",
        "domain",
        "related",
    ],
)
def test_missing_required_metadata_is_rejected(
    vault: Path,
    missing_field: str,
) -> None:
    lines = SYSTEMATIC_DEBUGGING.splitlines()

    filtered: list[str] = []
    skipping_related = False

    for line in lines:
        if missing_field == "related":
            if line.startswith("related:"):
                skipping_related = True
                continue

            if skipping_related:
                if line.startswith("  - ") or line.startswith("    "):
                    continue

                skipping_related = False

        if line.startswith(f"{missing_field}:"):
            continue

        filtered.append(line)

    write_skill(
        vault,
        "coding",
        "systematic-debugging",
        "\n".join(filtered),
    )

    runtime = SecondBrainRuntime(vault)

    result = runtime.get_skill(
        "coding.systematic-debugging",
        {"coding"},
    )

    assert isinstance(result, SecondBrainError)
    assert result.code == SecondBrainErrorCode.SKILL_VALIDATION_ERROR


def test_related_must_be_list(vault: Path) -> None:
    content = SYSTEMATIC_DEBUGGING.replace(
        """\
related:
  - skill: coding.test-driven-development
    description: "Useful once the root cause is known and the fix should be regression-tested."
""",
        'related: "coding.test-driven-development"\n',
    )

    write_skill(
        vault,
        "coding",
        "systematic-debugging",
        content,
    )

    runtime = SecondBrainRuntime(vault)

    result = runtime.get_skill(
        "coding.systematic-debugging",
        {"coding"},
    )

    assert isinstance(result, SecondBrainError)
    assert result.code == SecondBrainErrorCode.SKILL_VALIDATION_ERROR


def test_related_entry_requires_skill_and_description(
    vault: Path,
) -> None:
    content = SYSTEMATIC_DEBUGGING.replace(
        """\
related:
  - skill: coding.test-driven-development
    description: "Useful once the root cause is known and the fix should be regression-tested."
""",
        """\
related:
  - skill: coding.test-driven-development
""",
    )

    write_skill(
        vault,
        "coding",
        "systematic-debugging",
        content,
    )

    runtime = SecondBrainRuntime(vault)

    result = runtime.get_skill(
        "coding.systematic-debugging",
        {"coding"},
    )

    assert isinstance(result, SecondBrainError)
    assert result.code == SecondBrainErrorCode.SKILL_VALIDATION_ERROR


def test_invalid_related_skill_id_is_rejected(
    vault: Path,
) -> None:
    content = SYSTEMATIC_DEBUGGING.replace(
        "skill: coding.test-driven-development",
        "skill: ../../../other-file",
    )

    write_skill(
        vault,
        "coding",
        "systematic-debugging",
        content,
    )

    runtime = SecondBrainRuntime(vault)

    result = runtime.get_skill(
        "coding.systematic-debugging",
        {"coding"},
    )

    assert isinstance(result, SecondBrainError)
    assert result.code == SecondBrainErrorCode.SKILL_VALIDATION_ERROR


def test_empty_related_list_is_valid(
    runtime: SecondBrainRuntime,
) -> None:
    result = runtime.get_skill(
        "research.web-search",
        {"research"},
    )

    assert isinstance(result, LoadedSkill)
    assert result.metadata.related == ()
    assert result.projection.related == ()


def test_skill_projection_excludes_runtime_only_metadata(
    runtime: SecondBrainRuntime,
) -> None:
    result = runtime.get_skill(
        "coding.systematic-debugging",
        {"coding"},
    )

    assert isinstance(result, LoadedSkill)

    projection = result.projection

    assert not hasattr(projection, "skill_id")
    assert not hasattr(projection, "domain")
    assert not hasattr(projection, "author")
    assert not hasattr(projection, "license")
    assert not hasattr(projection, "version")


def test_loading_skill_does_not_modify_source_file(
    vault: Path,
) -> None:
    skill_path = (
        vault
        / "skills"
        / "coding"
        / "systematic-debugging"
        / "systematic-debugging.md"
    )

    before = skill_path.read_bytes()

    runtime = SecondBrainRuntime(vault)

    result = runtime.get_skill(
        "coding.systematic-debugging",
        {"coding"},
    )

    after = skill_path.read_bytes()

    assert isinstance(result, LoadedSkill)
    assert before == after
