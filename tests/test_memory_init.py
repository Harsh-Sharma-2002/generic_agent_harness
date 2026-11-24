"""Tests for the local memory bootstrap contract.

The initializer is exercised in a temporary repository root, never against
the developer's real ``vault/memory/`` state. No memory runtime exists yet,
so nothing beyond initialization is tested here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from second_brain.models import SecondBrainError
from second_brain.runtime import SecondBrainRuntime


REPO_ROOT = Path(__file__).resolve().parents[1]

INIT_SCRIPT = REPO_ROOT / "init_mem.sh"

VAULT_PATH = REPO_ROOT / "vault"

DOMAIN_IDS = {
    "coding": "memory.domain.coding",
    "research": "memory.domain.research",
    "data": "memory.domain.data",
    "teaching": "memory.domain.teaching",
}

USER_HEADINGS = (
    "## Stable preferences",
    "## Communication preferences",
    "## Cross-domain working preferences",
    "## Persistent constraints",
    "## Notes",
)

DOMAIN_HEADINGS = {
    "coding": (
        "## Workflow preferences",
        "## Code-change preferences",
        "## Testing and verification preferences",
        "## Architecture preferences",
        "## Review preferences",
        "## Repeated patterns",
        "## Notes",
    ),
    "research": (
        "## Source preferences",
        "## Retrieval preferences",
        "## Depth and stopping preferences",
        "## Synthesis preferences",
        "## Verification preferences",
        "## Repeated patterns",
        "## Notes",
    ),
    "data": (
        "## Analysis preferences",
        "## Query preferences",
        "## Validation preferences",
        "## Presentation preferences",
        "## Repeated patterns",
        "## Notes",
    ),
    "teaching": (
        "## Learning preferences",
        "## Current topic state",
        "## Demonstrated understanding",
        "## Recurring misconceptions",
        "## Hint dependence",
        "## Independence and transfer",
        "## Review needs",
        "## Repeated patterns",
        "## Notes",
    ),
}


# --------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------


def make_root(tmp_path: Path) -> Path:
    """Copy the initializer into a throwaway repository root."""

    root = tmp_path / "repo"

    root.mkdir()

    destination = root / INIT_SCRIPT.name

    shutil.copy2(INIT_SCRIPT, destination)

    destination.chmod(0o755)

    return root


def run_init(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(root / INIT_SCRIPT.name)],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def initialize(tmp_path: Path) -> Path:
    root = make_root(tmp_path)

    result = run_init(root)

    assert result.returncode == 0, result.stderr

    return root


def memory(root: Path) -> Path:
    return root / "vault" / "memory"


def snapshot(root: Path) -> dict[Path, bytes]:
    base = memory(root)

    return {
        path.relative_to(base): path.read_bytes()
        for path in sorted(base.rglob("*"))
        if path.is_file()
    }


def parse_profile(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")

    assert text.startswith("---\n")

    parts = text.split("---", 2)

    assert len(parts) == 3

    metadata = yaml.safe_load(parts[1])

    assert isinstance(metadata, dict)

    return metadata, parts[2]


def headings(body: str) -> list[str]:
    return [
        line.strip()
        for line in body.splitlines()
        if line.startswith("## ")
    ]


def sentinel(path: Path, marker: str) -> bytes:
    path.write_bytes(path.read_bytes() + f"\n{marker}\n".encode())

    return path.read_bytes()


# --------------------------------------------------------------------
# Initializer
# --------------------------------------------------------------------


def test_initializer_is_executable() -> None:
    assert INIT_SCRIPT.is_file()

    assert os.access(INIT_SCRIPT, os.X_OK)

    assert INIT_SCRIPT.stat().st_mode & 0o100


def test_fresh_initialization_creates_required_structure(
    tmp_path: Path,
) -> None:
    root = initialize(tmp_path)

    base = memory(root)

    assert (base / "USER.md").is_file()

    for domain in DOMAIN_IDS:
        assert (base / "domain_profiles" / f"{domain}.md").is_file()

    assert (base / "sessions").is_dir()

    assert list((base / "sessions").iterdir()) == []


def test_fresh_initialization_creates_nothing_else(
    tmp_path: Path,
) -> None:
    root = initialize(tmp_path)

    base = memory(root)

    # Architecture documentation is tracked outside runtime state.
    assert not (base / "README.md").exists()
    assert not (base / "domain_profiles" / "DOMAIN.md").exists()
    assert not (base / "sessions" / "README.md").exists()

    # The learner model lives in the teaching domain profile.
    assert not list(base.rglob("STUDENT.md"))

    # Session Memory is generic: no domain-scoped session directories.
    for domain in DOMAIN_IDS:
        assert not (base / "sessions" / domain).exists()

    for absent in (
        "rollouts",
        "episodic",
        "semantic",
        "vectors",
        "embeddings",
        "index.json",
        "memory.db",
    ):
        assert not (base / absent).exists()


# --------------------------------------------------------------------
# Idempotency and preservation
# --------------------------------------------------------------------


def test_second_run_is_byte_identical(tmp_path: Path) -> None:
    root = initialize(tmp_path)

    before = snapshot(root)

    result = run_init(root)

    assert result.returncode == 0, result.stderr

    assert snapshot(root) == before


def test_existing_user_profile_is_never_overwritten(
    tmp_path: Path,
) -> None:
    root = initialize(tmp_path)

    user = memory(root) / "USER.md"

    written = sentinel(user, "- sentinel-user-001")

    before = snapshot(root)

    result = run_init(root)

    assert result.returncode == 0, result.stderr

    assert user.read_bytes() == written

    assert snapshot(root) == before


def test_existing_domain_profiles_are_never_overwritten(
    tmp_path: Path,
) -> None:
    root = initialize(tmp_path)

    profiles = memory(root) / "domain_profiles"

    coding = sentinel(profiles / "coding.md", "- sentinel-coding-001")
    data = sentinel(profiles / "data.md", "- sentinel-data-001")

    before = snapshot(root)

    result = run_init(root)

    assert result.returncode == 0, result.stderr

    assert (profiles / "coding.md").read_bytes() == coding
    assert (profiles / "data.md").read_bytes() == data

    assert snapshot(root) == before


def test_partial_structure_is_repaired_without_touching_existing_memory(
    tmp_path: Path,
) -> None:
    root = initialize(tmp_path)

    base = memory(root)
    profiles = base / "domain_profiles"

    research = profiles / "research.md"
    teaching = profiles / "teaching.md"

    expected_research = research.read_bytes()
    expected_teaching = teaching.read_bytes()

    user_bytes = sentinel(base / "USER.md", "- sentinel-user-002")
    coding_bytes = sentinel(profiles / "coding.md", "- sentinel-coding-002")
    data_bytes = sentinel(profiles / "data.md", "- sentinel-data-002")

    research.unlink()
    teaching.unlink()

    result = run_init(root)

    assert result.returncode == 0, result.stderr

    # Only the missing files were recreated, byte for byte.
    assert research.read_bytes() == expected_research
    assert teaching.read_bytes() == expected_teaching

    # Existing memory was not touched.
    assert (base / "USER.md").read_bytes() == user_bytes
    assert (profiles / "coding.md").read_bytes() == coding_bytes
    assert (profiles / "data.md").read_bytes() == data_bytes


def test_missing_sessions_directory_is_repaired(
    tmp_path: Path,
) -> None:
    root = initialize(tmp_path)

    base = memory(root)

    sentinel(base / "USER.md", "- sentinel-user-003")

    before = snapshot(root)

    shutil.rmtree(base / "sessions")

    result = run_init(root)

    assert result.returncode == 0, result.stderr

    assert (base / "sessions").is_dir()

    assert list((base / "sessions").iterdir()) == []

    # Profile memory is untouched.
    assert snapshot(root) == before


# --------------------------------------------------------------------
# Canonical generated schema
# --------------------------------------------------------------------


def test_generated_user_profile_schema(tmp_path: Path) -> None:
    root = initialize(tmp_path)

    metadata, body = parse_profile(memory(root) / "USER.md")

    assert metadata == {
        "name": "user-profile",
        "id": "memory.user",
        "scope": "global",
        "lifetime": "durable",
    }

    assert body.lstrip().startswith("# User Profile")

    assert headings(body) == list(USER_HEADINGS)


@pytest.mark.parametrize("domain", sorted(DOMAIN_IDS))
def test_generated_domain_profile_schema(
    tmp_path: Path,
    domain: str,
) -> None:
    root = initialize(tmp_path)

    metadata, body = parse_profile(
        memory(root) / "domain_profiles" / f"{domain}.md"
    )

    assert metadata == {
        "name": f"{domain}-profile",
        "id": DOMAIN_IDS[domain],
        "domain": domain,
        "scope": "domain",
        "lifetime": "durable",
    }

    assert body.lstrip().startswith(
        f"# {domain.capitalize()} Profile"
    )

    assert headings(body) == list(DOMAIN_HEADINGS[domain])


# --------------------------------------------------------------------
# Role isolation
# --------------------------------------------------------------------


def test_memory_is_not_part_of_the_worker_skill_corpus() -> None:
    # Memory is not a methodology domain; the Worker loader only
    # resolves vault/skills and must not see vault/memory.
    runtime = SecondBrainRuntime(VAULT_PATH)

    assert runtime.skills_path == VAULT_PATH / "skills"

    assert (
        runtime.supervisor_skills_path
        == VAULT_PATH / "supervisor_skills"
    )

    assert isinstance(runtime.get_domain("memory"), SecondBrainError)
