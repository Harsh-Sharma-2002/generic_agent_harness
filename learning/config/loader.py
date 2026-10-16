"""Configuration loader for KernelAI offline learning."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from learning.models import LearningMode


CONFIG_PATH = Path(__file__).with_name("learning.yaml")


@dataclass(frozen=True)
class PolicyLearningConfig:
    """Learning configuration for one production policy."""

    learning: LearningMode


@dataclass(frozen=True)
class LearningConfig:
    """Validated configuration for KernelAI offline learning."""

    enabled: bool
    minimum_samples: int
    policies: dict[str, PolicyLearningConfig]


def load_learning_config(
    path: Path = CONFIG_PATH,
) -> LearningConfig:
    """Load and validate the offline learning configuration."""

    if not path.exists():
        raise FileNotFoundError(
            f"Learning configuration not found: {path}"
        )

    with path.open("r", encoding="utf-8") as file:
        raw: Any = yaml.safe_load(file)

    if not isinstance(raw, dict):
        raise ValueError(
            "Learning configuration must be a mapping."
        )

    learning = raw.get("learning")

    if not isinstance(learning, dict):
        raise ValueError(
            "Learning configuration must contain a "
            "'learning' mapping."
        )

    enabled = learning.get("enabled")

    if not isinstance(enabled, bool):
        raise ValueError(
            "'learning.enabled' must be a boolean."
        )

    minimum_samples = learning.get("minimum_samples")

    if (
        not isinstance(minimum_samples, int)
        or isinstance(minimum_samples, bool)
        or minimum_samples < 1
    ):
        raise ValueError(
            "'learning.minimum_samples' must be an integer "
            "greater than or equal to 1."
        )

    raw_policies = learning.get("policies")

    if not isinstance(raw_policies, dict):
        raise ValueError(
            "'learning.policies' must be a mapping."
        )

    policies: dict[str, PolicyLearningConfig] = {}

    for policy_name, policy_config in raw_policies.items():
        if (
            not isinstance(policy_name, str)
            or not policy_name.strip()
        ):
            raise ValueError(
                "Policy names must be non-empty strings."
            )

        if not isinstance(policy_config, dict):
            raise ValueError(
                f"Configuration for policy "
                f"{policy_name!r} must be a mapping."
            )

        raw_mode = policy_config.get("learning")

        try:
            mode = LearningMode(raw_mode)
        except (TypeError, ValueError) as exc:
            valid_modes = ", ".join(
                mode.value for mode in LearningMode
            )

            raise ValueError(
                f"Invalid learning mode for policy "
                f"{policy_name!r}: {raw_mode!r}. "
                f"Expected one of: {valid_modes}."
            ) from exc

        policies[policy_name] = PolicyLearningConfig(
            learning=mode,
        )

    return LearningConfig(
        enabled=enabled,
        minimum_samples=minimum_samples,
        policies=policies,
    )