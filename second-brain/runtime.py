"""Read-only runtime access to the KernelAI Second Brain vault."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import (
    LoadedSkill,
    RelatedSkill,
    SecondBrainError,
    SecondBrainErrorCode,
    SkillMetadata,
    SkillProjection,
)


class SecondBrainRuntime:
    """
    Read-only interface to the Second Brain vault.

    Responsibilities:
    - Read REGISTRY.md.
    - Read DOMAIN.md files.
    - Resolve canonical skill IDs.
    - Lazily read and parse skill files.
    - Validate canonical skill metadata.
    - Produce runtime metadata and model-facing skill projections.

    This runtime does NOT own:
    - Task state.
    - Skill load limits.
    - Escalation.
    - Tool authority.
    - Worker progress.
    """

    def __init__(self, vault_path: str | Path) -> None:
        self.vault_path = Path(vault_path)
        self.skills_path = self.vault_path / "skills"

    def get_registry(self) -> str | SecondBrainError:
        """Return the global skill registry."""

        registry_path = self.skills_path / "REGISTRY.md"

        if not registry_path.is_file():
            return SecondBrainError(
                code=SecondBrainErrorCode.REGISTRY_NOT_FOUND,
                message="Second Brain REGISTRY.md was not found.",
            )

        try:
            return registry_path.read_text(encoding="utf-8")
        except OSError as exc:
            return SecondBrainError(
                code=SecondBrainErrorCode.REGISTRY_NOT_FOUND,
                message=f"Could not read REGISTRY.md: {exc}",
            )

    def get_domain(self, domain: str) -> str | SecondBrainError:
        """Return the DOMAIN.md document for a domain."""

        if not self._valid_segment(domain):
            return SecondBrainError(
                code=SecondBrainErrorCode.DOMAIN_NOT_FOUND,
                message=f"Invalid domain '{domain}'.",
            )

        domain_path = self.skills_path / domain / "DOMAIN.md"

        if not domain_path.is_file():
            return SecondBrainError(
                code=SecondBrainErrorCode.DOMAIN_NOT_FOUND,
                message=f"Domain '{domain}' does not exist.",
            )

        try:
            return domain_path.read_text(encoding="utf-8")
        except OSError as exc:
            return SecondBrainError(
                code=SecondBrainErrorCode.DOMAIN_NOT_FOUND,
                message=f"Could not read domain '{domain}': {exc}",
            )

    def get_skill(
        self,
        skill_id: str,
        authorized_domains: set[str],
    ) -> LoadedSkill | SecondBrainError:
        """
        Load and project one skill from an authorized domain.

        Canonical skill IDs use:

            <domain>.<skill-name>

        Example:

            coding.systematic-debugging
        """

        parsed_id = self._parse_skill_id(skill_id)

        if parsed_id is None:
            return SecondBrainError(
                code=SecondBrainErrorCode.SKILL_VALIDATION_ERROR,
                message=(
                    f"Invalid skill ID '{skill_id}'. "
                    "Expected '<domain>.<skill-name>'."
                ),
            )

        domain, skill_name = parsed_id

        if domain not in authorized_domains:
            return SecondBrainError(
                code=SecondBrainErrorCode.UNAUTHORIZED_SKILL,
                message=(
                    f"Skill '{skill_id}' belongs to unauthorized "
                    f"domain '{domain}'."
                ),
            )

        skill_path = (
            self.skills_path
            / domain
            / skill_name
            / f"{skill_name}.md"
        )

        if not skill_path.is_file():
            return SecondBrainError(
                code=SecondBrainErrorCode.SKILL_NOT_FOUND,
                message=f"Skill '{skill_id}' does not exist.",
            )

        try:
            raw_document = skill_path.read_text(encoding="utf-8")
        except OSError as exc:
            return SecondBrainError(
                code=SecondBrainErrorCode.SKILL_PARSE_ERROR,
                message=f"Could not read skill '{skill_id}': {exc}",
            )

        parsed_document = self._parse_document(raw_document)

        if isinstance(parsed_document, SecondBrainError):
            return parsed_document

        frontmatter, body = parsed_document

        metadata = self._parse_metadata(frontmatter)

        if isinstance(metadata, SecondBrainError):
            return metadata

        validation_error = self._validate_metadata(
            metadata=metadata,
            expected_domain=domain,
            expected_name=skill_name,
            expected_skill_id=skill_id,
        )

        if validation_error is not None:
            return validation_error

        projected_body = self._strip_related_skills_section(body)

        projection = SkillProjection(
            name=metadata.name,
            description=metadata.description,
            content=projected_body,
            related=metadata.related,
        )

        return LoadedSkill(
            metadata=metadata,
            projection=projection,
        )

    @staticmethod
    def _parse_skill_id(skill_id: str) -> tuple[str, str] | None:
        """Parse a canonical '<domain>.<skill-name>' identifier."""

        if not isinstance(skill_id, str):
            return None

        parts = skill_id.split(".")

        if len(parts) != 2:
            return None

        domain, skill_name = parts

        if not (
            SecondBrainRuntime._valid_segment(domain)
            and SecondBrainRuntime._valid_segment(skill_name)
        ):
            return None

        return domain, skill_name

    @staticmethod
    def _valid_segment(value: str) -> bool:
        """
        Validate one canonical ID/path segment.

        V1 permits:
        - lowercase ASCII letters
        - digits
        - hyphens
        - underscores

        This also prevents arbitrary filesystem traversal.
        """

        if not isinstance(value, str) or not value:
            return False

        return all(
            ("a" <= char <= "z")
            or ("0" <= char <= "9")
            or char in {"-", "_"}
            for char in value
        )

    @staticmethod
    def _parse_document(
        document: str,
    ) -> tuple[dict[str, Any], str] | SecondBrainError:
        """Split YAML frontmatter from the Markdown body."""

        if not document.startswith("---"):
            return SecondBrainError(
                code=SecondBrainErrorCode.SKILL_PARSE_ERROR,
                message="Skill file is missing YAML frontmatter.",
            )

        parts = document.split("---", 2)

        if len(parts) != 3:
            return SecondBrainError(
                code=SecondBrainErrorCode.SKILL_PARSE_ERROR,
                message="Skill YAML frontmatter is not properly closed.",
            )

        yaml_text = parts[1]
        body = parts[2].lstrip("\n")

        try:
            frontmatter = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            return SecondBrainError(
                code=SecondBrainErrorCode.SKILL_PARSE_ERROR,
                message=f"Invalid skill YAML: {exc}",
            )

        if not isinstance(frontmatter, dict):
            return SecondBrainError(
                code=SecondBrainErrorCode.SKILL_PARSE_ERROR,
                message="Skill YAML frontmatter must be an object.",
            )

        return frontmatter, body

    @staticmethod
    def _parse_metadata(
        frontmatter: dict[str, Any],
    ) -> SkillMetadata | SecondBrainError:
        """Convert raw YAML frontmatter into typed skill metadata."""

        required_fields = (
            "id",
            "name",
            "domain",
            "description",
            "related",
        )

        for field in required_fields:
            if field not in frontmatter:
                return SecondBrainError(
                    code=SecondBrainErrorCode.SKILL_VALIDATION_ERROR,
                    message=f"Skill is missing required field '{field}'.",
                )

        skill_id = frontmatter["id"]
        name = frontmatter["name"]
        domain = frontmatter["domain"]
        description = frontmatter["description"]
        raw_related = frontmatter["related"]

        string_fields = {
            "id": skill_id,
            "name": name,
            "domain": domain,
            "description": description,
        }

        for field_name, value in string_fields.items():
            if not isinstance(value, str) or not value.strip():
                return SecondBrainError(
                    code=SecondBrainErrorCode.SKILL_VALIDATION_ERROR,
                    message=(
                        f"Skill '{field_name}' must be a "
                        "non-empty string."
                    ),
                )

        if not isinstance(raw_related, list):
            return SecondBrainError(
                code=SecondBrainErrorCode.SKILL_VALIDATION_ERROR,
                message="Skill 'related' must be a list.",
            )

        related: list[RelatedSkill] = []

        for index, item in enumerate(raw_related):
            if not isinstance(item, dict):
                return SecondBrainError(
                    code=SecondBrainErrorCode.SKILL_VALIDATION_ERROR,
                    message=f"related[{index}] must be an object.",
                )

            related_skill_id = item.get("skill")
            related_description = item.get("description")

            if (
                not isinstance(related_skill_id, str)
                or not related_skill_id.strip()
            ):
                return SecondBrainError(
                    code=SecondBrainErrorCode.SKILL_VALIDATION_ERROR,
                    message=(
                        f"related[{index}].skill must be a "
                        "non-empty string."
                    ),
                )

            if (
                not isinstance(related_description, str)
                or not related_description.strip()
            ):
                return SecondBrainError(
                    code=SecondBrainErrorCode.SKILL_VALIDATION_ERROR,
                    message=(
                        f"related[{index}].description must be a "
                        "non-empty string."
                    ),
                )

            if SecondBrainRuntime._parse_skill_id(
                related_skill_id
            ) is None:
                return SecondBrainError(
                    code=SecondBrainErrorCode.SKILL_VALIDATION_ERROR,
                    message=(
                        f"related[{index}].skill "
                        f"'{related_skill_id}' is not a valid "
                        "canonical skill ID."
                    ),
                )

            related.append(
                RelatedSkill(
                    skill_id=related_skill_id,
                    description=related_description,
                )
            )

        return SkillMetadata(
            skill_id=skill_id,
            name=name,
            domain=domain,
            description=description,
            related=tuple(related),
        )

    @staticmethod
    def _validate_metadata(
        metadata: SkillMetadata,
        expected_domain: str,
        expected_name: str,
        expected_skill_id: str,
    ) -> SecondBrainError | None:
        """
        Validate skill identity against its canonical filesystem location.

        Example path:

            coding/systematic-debugging/systematic-debugging.md

        must contain:

            name: systematic-debugging
            domain: coding
            id: coding.systematic-debugging
        """

        if metadata.domain != expected_domain:
            return SecondBrainError(
                code=SecondBrainErrorCode.SKILL_VALIDATION_ERROR,
                message=(
                    "Skill domain mismatch: "
                    f"expected '{expected_domain}', "
                    f"found '{metadata.domain}'."
                ),
            )

        if metadata.name != expected_name:
            return SecondBrainError(
                code=SecondBrainErrorCode.SKILL_VALIDATION_ERROR,
                message=(
                    "Skill name mismatch: "
                    f"expected '{expected_name}', "
                    f"found '{metadata.name}'."
                ),
            )

        if metadata.skill_id != expected_skill_id:
            return SecondBrainError(
                code=SecondBrainErrorCode.SKILL_VALIDATION_ERROR,
                message=(
                    "Skill ID mismatch: "
                    f"expected '{expected_skill_id}', "
                    f"found '{metadata.skill_id}'."
                ),
            )

        return None

    @staticmethod
    def _strip_related_skills_section(body: str) -> str:
        """
        Remove the Obsidian-only '## Related Skills' section.

        Structured YAML `related` metadata is already returned separately,
        so the visualization-only wikilinks should not consume model context.
        """

        lines = body.splitlines()
        result: list[str] = []

        skipping = False

        for line in lines:
            if line.strip() == "## Related Skills":
                skipping = True
                continue

            if skipping and line.startswith("## "):
                skipping = False

            if not skipping:
                result.append(line)

        return "\n".join(result).strip()