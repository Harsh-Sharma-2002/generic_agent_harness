"""Manual policy release boundary for KernelAI offline learning.

A release promotes an existing learning candidate into production.

Before production is changed, the currently deployed policy is copied
into the release registry so the previous production state is preserved.

This module is intentionally NOT exposed to the Learning Supervisor.
Candidate creation is automated; production release is a separate,
manual operation.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from learning.policies.store import (
    CANDIDATE_ROOT,
    POLICY_PATHS,
    read_current_policy,
    read_policy_candidate,
)


LEARNING_ROOT = Path(__file__).parent

REGISTRY_ROOT = (
    LEARNING_ROOT
    / "data"
    / "registry"
)


def release_candidate(
    *,
    policy_name: str,
    candidate_id: str,
) -> dict[str, Any]:
    """
    Manually promote one policy candidate into production.

    Release procedure:

    1. validate the policy and candidate,
    2. read the current production policy,
    3. preserve the current production policy in the release registry,
    4. atomically replace the production policy with the candidate,
    5. persist release metadata.

    Args:
        policy_name:
            Known production policy to replace.

        candidate_id:
            Existing candidate identifier produced by the learning
            system.

    Returns:
        Release metadata including the release identifier and paths.

    This function is intentionally not available to the Learning
    Supervisor.
    """

    production_path = _production_path(
        policy_name
    )

    candidate_content = (
        read_policy_candidate(
            policy_name,
            candidate_id,
        )
    )

    current_content = (
        read_current_policy(
            policy_name
        )
    )

    release_id = _new_release_id()

    registry_directory = (
        REGISTRY_ROOT
        / policy_name
    )

    registry_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    previous_policy_path = (
        registry_directory
        / f"{release_id}_previous.md"
    )

    metadata_path = (
        registry_directory
        / f"{release_id}.json"
    )

    previous_policy_path.write_text(
        current_content,
        encoding="utf-8",
    )

    _atomic_write(
        path=production_path,
        content=candidate_content,
    )

    metadata = {
        "release_id": release_id,
        "policy_name": policy_name,
        "candidate_id": candidate_id,
        "released_at": (
            datetime.now(
                timezone.utc
            ).isoformat()
        ),
        "previous_policy": (
            previous_policy_path.name
        ),
        "production_path": str(
            production_path
        ),
    }

    metadata_path.write_text(
        json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return metadata


def _production_path(
    policy_name: str,
) -> Path:
    """Resolve a known policy name to its production file."""

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

    if not path.exists():
        raise FileNotFoundError(
            f"Production policy does not exist: {path}"
        )

    if not path.is_file():
        raise ValueError(
            f"Production policy path is not a file: {path}"
        )

    return path


def _atomic_write(
    *,
    path: Path,
    content: str,
) -> None:
    """
    Replace one production policy using a temporary file followed by
    an atomic filesystem replacement.
    """

    temporary_path = path.with_name(
        f".{path.name}.release.tmp"
    )

    try:
        temporary_path.write_text(
            content,
            encoding="utf-8",
        )

        os.replace(
            temporary_path,
            path,
        )

    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _new_release_id() -> str:
    """Generate a unique, sortable release identifier."""

    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )

    return (
        f"release_{timestamp}"
    )
