"""Controlled policy storage for KernelAI offline learning.

This module provides the learning system with safe access to
production policies and learning artifacts.

Responsibilities:

- resolve known policy names to production files,
- read current production policy content,
- create immutable historical snapshots,
- read historical policy versions,
- create candidate policy files.

This module deliberately does NOT provide any operation that writes
to production policy files. Candidate creation and production release
are separate capabilities.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# Repository paths
# ============================================================


REPOSITORY_ROOT = (
    Path(__file__).resolve().parents[2]
)

LEARNING_ROOT = (
    REPOSITORY_ROOT
    / "learning"
)

VERSION_ROOT = (
    LEARNING_ROOT
    / "data"
    / "versions"
)

CANDIDATE_ROOT = (
    LEARNING_ROOT
    / "data"
    / "candidates"
)


# ============================================================
# Production policy registry
# ============================================================


POLICY_PATHS: dict[str, Path] = {
    "web_search": (
        REPOSITORY_ROOT
        / "skills"
        / "web_search.md"
    ),
    "text2sql": (
        REPOSITORY_ROOT
        / "skills"
        / "text2sql.md"
    ),
    "supervisor": (
        REPOSITORY_ROOT
        / "supervisor"
        / "supervisor.md"
    ),
    "task_worker": (
        REPOSITORY_ROOT
        / "tasks"
        / "task_worker.md"
    ),
}


# ============================================================
# Public models
# ============================================================


def list_policies() -> tuple[str, ...]:
    """
    Return the policy names recognized by the learning system.

    Policy names are semantic identifiers. Callers never provide
    arbitrary filesystem paths.
    """

    return tuple(
        POLICY_PATHS.keys()
    )


def read_current_policy(
    policy_name: str,
) -> str:
    """
    Read the current production content of one known policy.

    This operation is read-only. It never modifies the production
    policy.

    Args:
        policy_name:
            Known policy identifier such as:
            - "web_search"
            - "text2sql"
            - "supervisor"
            - "task_worker"

    Returns:
        Complete current production policy text.
    """

    path = _production_policy_path(
        policy_name
    )

    if not path.exists():
        raise FileNotFoundError(
            f"Production policy file does not exist "
            f"for {policy_name!r}: {path}"
        )

    if not path.is_file():
        raise ValueError(
            f"Production policy path is not a file "
            f"for {policy_name!r}."
        )

    return path.read_text(
        encoding="utf-8"
    )


def snapshot_current_policy(
    policy_name: str,
) -> str:
    """
    Create an immutable historical snapshot of the current
    production policy.

    Snapshots are stored under:

        learning/data/versions/<policy_name>/

    This operation never modifies the production policy.

    Returns:
        The generated version identifier.
    """

    content = read_current_policy(
        policy_name
    )

    policy_directory = (
        VERSION_ROOT
        / policy_name
    )

    policy_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    version_id = _new_version_id()

    path = (
        policy_directory
        / f"{version_id}.md"
    )

    if path.exists():
        raise FileExistsError(
            f"Policy version already exists: "
            f"{version_id}"
        )

    path.write_text(
        content,
        encoding="utf-8",
    )

    return version_id


def read_policy_version(
    policy_name: str,
    version_id: str,
) -> str:
    """
    Read one historical policy snapshot.

    Args:
        policy_name:
            Known policy identifier.

        version_id:
            Version identifier previously returned by
            snapshot_current_policy().

    Returns:
        Complete historical policy content.
    """

    _production_policy_path(
        policy_name
    )

    _validate_identifier(
        version_id,
        field="version_id",
    )

    path = (
        VERSION_ROOT
        / policy_name
        / f"{version_id}.md"
    )

    if not path.exists():
        raise FileNotFoundError(
            f"Policy version not found: "
            f"{policy_name!r} / {version_id!r}"
        )

    if not path.is_file():
        raise ValueError(
            "Policy version path is not a file."
        )

    return path.read_text(
        encoding="utf-8"
    )


def create_policy_candidate(
    policy_name: str,
    content: str,
) -> str:
    """
    Store a proposed replacement for one production policy.

    Candidates are written only under:

        learning/data/candidates/<policy_name>/

    This function NEVER writes to the production policy file.

    Creating a candidate therefore does not change KernelAI runtime
    behavior.

    Args:
        policy_name:
            Known production policy being targeted.

        content:
            Complete proposed policy content.

    Returns:
        Generated candidate identifier.
    """

    _production_policy_path(
        policy_name
    )

    if not isinstance(
        content,
        str,
    ):
        raise TypeError(
            "Candidate content must be a string."
        )

    if not content.strip():
        raise ValueError(
            "Candidate content must not be empty."
        )

    policy_directory = (
        CANDIDATE_ROOT
        / policy_name
    )

    policy_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    candidate_id = (
        _new_candidate_id()
    )

    path = (
        policy_directory
        / f"{candidate_id}.md"
    )

    if path.exists():
        raise FileExistsError(
            f"Policy candidate already exists: "
            f"{candidate_id}"
        )

    path.write_text(
        content,
        encoding="utf-8",
    )

    return candidate_id


def read_policy_candidate(
    policy_name: str,
    candidate_id: str,
) -> str:
    """
    Read one previously created policy candidate.

    This operation is read-only and does not affect production.
    """

    _production_policy_path(
        policy_name
    )

    _validate_identifier(
        candidate_id,
        field="candidate_id",
    )

    path = (
        CANDIDATE_ROOT
        / policy_name
        / f"{candidate_id}.md"
    )

    if not path.exists():
        raise FileNotFoundError(
            f"Policy candidate not found: "
            f"{policy_name!r} / {candidate_id!r}"
        )

    if not path.is_file():
        raise ValueError(
            "Policy candidate path is not a file."
        )

    return path.read_text(
        encoding="utf-8"
    )


# ============================================================
# Policy resolution
# ============================================================


def _production_policy_path(
    policy_name: str,
) -> Path:
    """
    Resolve a semantic policy name to its production file.

    Arbitrary filesystem paths are never accepted.
    """

    if not isinstance(
        policy_name,
        str,
    ):
        raise TypeError(
            "policy_name must be a string."
        )

    policy_name = (
        policy_name.strip()
    )

    if not policy_name:
        raise ValueError(
            "policy_name must not be empty."
        )

    path = POLICY_PATHS.get(
        policy_name
    )

    if path is None:
        valid = ", ".join(
            POLICY_PATHS
        )

        raise ValueError(
            f"Unknown policy {policy_name!r}. "
            f"Expected one of: {valid}."
        )

    return path


# ============================================================
# Identifier validation
# ============================================================


def _validate_identifier(
    value: str,
    *,
    field: str,
) -> None:
    """
    Validate an opaque local-storage identifier.

    Identifiers are not filesystem paths.
    """

    if not isinstance(
        value,
        str,
    ):
        raise TypeError(
            f"{field} must be a string."
        )

    if not value.strip():
        raise ValueError(
            f"{field} must not be empty."
        )

    if (
        "/" in value
        or "\\" in value
        or ".." in value
    ):
        raise ValueError(
            f"Invalid {field}."
        )


# ============================================================
# Identifier generation
# ============================================================


def _new_version_id() -> str:
    """
    Generate a unique, time-sortable policy version identifier.
    """

    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )

    return (
        f"version_{timestamp}"
    )


def _new_candidate_id() -> str:
    """
    Generate a unique, time-sortable candidate identifier.
    """

    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )

    return (
        f"candidate_{timestamp}"
    )