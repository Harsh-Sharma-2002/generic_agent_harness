"""Core models shared by KernelAI's offline learning system."""

from __future__ import annotations

from enum import Enum


class LearningMode(str, Enum):
    """
    Controls whether the offline learning system may propose changes
    to a production policy.
    """

    ENABLED = "enabled"
    MANUAL_ONLY = "manual_only"
    DISABLED = "disabled"


class TracePlane(str, Enum):
    """
    Identifies which execution plane produced a trace.

    Learning and evaluation traces must remain distinguishable from
    production traces so the learner does not learn from its own work.
    """

    PRODUCTION = "production"
    LEARNING = "learning"
    EVALUATION = "evaluation"