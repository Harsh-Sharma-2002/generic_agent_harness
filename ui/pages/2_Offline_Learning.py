"""KernelAI offline learning dashboard.

Read-only presentation of already-persisted learning state:
production traces, evaluations, policies, versions, candidates,
and releases. This page never invokes the learning pipeline and
never mutates anything.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pandas as pd
import streamlit as st


# ==========================================================
# Configuration
# ==========================================================

API_BASE_URL = os.getenv(
    "API_BASE_URL",
    "http://127.0.0.1:8000",
)


# ==========================================================
# Page Configuration
# ==========================================================

st.set_page_config(
    page_title="KernelAI Offline Learning",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ==========================================================
# API Helpers
# ==========================================================


def fetch_learning_dashboard() -> dict[str, Any]:
    """
    Fetch the read-only offline-learning dashboard state.
    """

    response = httpx.get(
        f"{API_BASE_URL}/dashboard/learning",
        timeout=10.0,
    )

    response.raise_for_status()

    return response.json()


# ==========================================================
# Formatting Helpers
# ==========================================================

METRIC_LABELS = {
    "completion": "Completion",
    "verified_success": "Verified Success",
    "schema_violations": "Schema Violations",
    "policy_violations": "Policy Violations",
    "timeouts": "Timeouts",
    "policy_failures": "Policy Failures",
    "provider_failures": "Provider Failures",
    "runtime_failures": "Runtime Failures",
    "tool_calls": "Tool Calls",
    "llm_calls": "LLM Calls",
    "input_tokens": "Input Tokens",
    "output_tokens": "Output Tokens",
    "latency_seconds": "Latency",
    "exact_duplicate_calls": "Exact Duplicate Calls",
    "unchanged_retries": "Unchanged Retries",
    "task_fulfillment": "Task Fulfillment",
    "requirement_coverage": "Requirement Coverage",
    "supported_claim_rate": "Supported Claim Rate",
    "contradicted_claim_rate": "Contradicted Claim Rate",
    "unverifiable_claim_rate": "Unverifiable Claim Rate",
    "scope_overreach": "Scope Overreach",
    "sufficiency_stopping": "Sufficiency Stopping",
    "policy_compliance": "Policy Compliance",
}

POLICY_LABELS = {
    "web_search": "Web Search",
    "text2sql": "Text2SQL",
    "task_worker": "Task Worker",
    "supervisor": "Supervisor",
}

HIGHER_IS_BETTER = frozenset(
    {
        "completion",
        "task_fulfillment",
        "requirement_coverage",
        "supported_claim_rate",
        "sufficiency_stopping",
        "policy_compliance",
    }
)

LOWER_IS_BETTER = frozenset(
    {
        "contradicted_claim_rate",
        "unverifiable_claim_rate",
        "scope_overreach",
        "tool_calls",
        "llm_calls",
        "latency_seconds",
        "exact_duplicate_calls",
        "unchanged_retries",
        "policy_violations",
    }
)

MODE_DISPLAY = {
    "enabled": "ENABLED",
    "manual_only": "MANUAL ONLY",
    "disabled": "DISABLED",
}

MODE_ICONS = {
    "enabled": "🟢",
    "manual_only": "🟡",
    "disabled": "⚪",
}

DASH = "—"


def humanize_metric(
    name: str,
) -> str:
    """Return a human-readable metric label."""

    return METRIC_LABELS.get(
        name,
        name.replace("_", " ").title(),
    )


def humanize_policy(
    name: str,
) -> str:
    """Return a human-readable policy label."""

    return POLICY_LABELS.get(
        name,
        name.replace("_", " ").title(),
    )


def metric_direction(
    name: str,
) -> str:
    """Return display-only direction guidance for one metric."""

    if name in HIGHER_IS_BETTER:
        return "Higher is generally better."

    if name in LOWER_IS_BETTER:
        return "Lower is generally better."

    return "Direction depends on metric."


def format_number(
    value: Any,
) -> str:
    """Format one metric value for compact display."""

    if value is None:
        return DASH

    if isinstance(value, bool):
        return str(int(value))

    if isinstance(value, int):
        return str(value)

    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))

        return f"{value:.3f}"

    return str(value)


def or_dash(
    value: Any,
) -> str:
    """Render missing values as an em dash."""

    if value is None or value == "":
        return DASH

    return str(value)


def short_id(
    value: str | None,
) -> str:
    """Shorten long artifact IDs for compact display."""

    if not value:
        return DASH

    if len(value) <= 20:
        return value

    return value[:17] + "..."


def metric_aggregate(
    evaluation: dict[str, Any],
    metric_name: str,
) -> dict[str, Any] | None:
    """
    Return one metric's aggregate from one evaluation, or None.
    """

    for metric in evaluation.get(
        "aggregate_metrics",
        [],
    ):
        if metric.get("name") == metric_name:
            return metric

    return None


# ==========================================================
# Sidebar
# ==========================================================

with st.sidebar:
    st.header(
        "KernelAI"
    )

    st.caption(
        "Navigation"
    )

    navigation_columns = st.columns(
        3
    )

    with navigation_columns[0]:
        if st.button(
            "💬 Chat",
            use_container_width=True,
        ):
            st.switch_page(
                "app.py"
            )

    with navigation_columns[1]:
        if st.button(
            "📊 Traces",
            use_container_width=True,
        ):
            st.switch_page(
                "pages/1_Observability.py"
            )

    with navigation_columns[2]:
        st.button(
            "📈 Learning",
            use_container_width=True,
            disabled=True,
        )

    st.divider()

    st.caption(
        "Offline Learning"
    )

    st.markdown(
        """
        Historical evaluation, policy versions,
        and learning state.
        """
    )

    st.caption(
        "Read-only view. No learning actions are exposed."
    )


# ==========================================================
# Header
# ==========================================================

st.title(
    "Offline Learning"
)

st.caption(
    "Historical evaluation, policy versions, and learning state."
)


# ==========================================================
# Fetch Dashboard State
# ==========================================================

try:
    dashboard = fetch_learning_dashboard()

except httpx.HTTPError:
    st.error(
        "KernelAI API is unavailable."
    )

    st.stop()


dashboard_policies = dashboard.get(
    "policies",
    [],
)

dashboard_summary = dashboard.get(
    "summary",
    {},
)

evaluation_history = dashboard.get(
    "evaluation_history",
    [],
)

latest_evaluation = dashboard.get(
    "latest_evaluation"
)


# ==========================================================
# Summary
# ==========================================================

learning_enabled = dashboard.get(
    "enabled"
)

summary_columns = st.columns(
    5
)

with summary_columns[0]:
    st.metric(
        "Status",
        (
            "ENABLED"
            if learning_enabled
            else "DISABLED"
        ),
    )

with summary_columns[1]:
    st.metric(
        "Production Executions",
        dashboard.get(
            "production_execution_count",
            0,
        ),
    )

with summary_columns[2]:
    st.metric(
        "Evaluations",
        dashboard_summary.get(
            "evaluation_count",
            0,
        ),
    )

with summary_columns[3]:
    st.metric(
        "Candidates",
        dashboard_summary.get(
            "candidate_count",
            0,
        ),
    )

with summary_columns[4]:
    st.metric(
        "Releases",
        dashboard_summary.get(
            "release_count",
            0,
        ),
    )


# ==========================================================
# Policy State
# ==========================================================

st.divider()

st.subheader(
    "Policy State"
)

if not dashboard_policies:

    st.caption(
        "No known policies are configured."
    )


for index, policy in enumerate(
    dashboard_policies
):
    policy_name = policy["policy_name"]

    current = policy.get(
        "current",
        {},
    ) or {}

    current_version = current.get(
        "version_id"
    )

    st.markdown(
        f"#### "
        f"{MODE_ICONS.get(policy.get('learning_mode'), '⚪')} "
        f"{humanize_policy(policy_name)}"
    )

    policy_columns = st.columns(
        4
    )

    with policy_columns[0]:
        st.metric(
            "Learning mode",
            MODE_DISPLAY.get(
                policy.get("learning_mode"),
                "DISABLED",
            ),
        )

    with policy_columns[1]:
        st.metric(
            "Current production",
            (
                short_id(current_version)
                if current_version
                else "unversioned production"
            ),
        )

    with policy_columns[2]:
        st.metric(
            "Versions",
            policy.get("version_count", 0),
        )

    with policy_columns[3]:
        st.metric(
            "Candidates",
            policy.get("candidate_count", 0),
        )

    st.caption(
        "Learning: "
        + MODE_DISPLAY.get(
            policy.get("learning_mode"),
            "DISABLED",
        )
        + "  ·  Latest candidate: "
        + or_dash(
            policy.get("latest_candidate_id")
        )
        + "  ·  Latest release: "
        + or_dash(
            policy.get("latest_release_id")
        )
    )

    if index < len(dashboard_policies) - 1:
        st.divider()


# ==========================================================
# Latest Evaluation
# ==========================================================

st.divider()

st.subheader(
    "Latest Evaluation"
)

if latest_evaluation is None:

    st.info(
        "No offline evaluation has been recorded yet."
    )

    if (
        dashboard.get(
            "production_execution_count",
            0,
        )
        > 0
    ):

        st.caption(
            "Production traces are available. "
            "No offline evaluation has been recorded yet."
        )


else:

    evaluation_columns = st.columns(
        5
    )

    with evaluation_columns[0]:
        st.metric(
            "Evaluation",
            short_id(
                latest_evaluation.get("evaluation_id")
            ),
        )

    with evaluation_columns[1]:
        st.metric(
            "Executions",
            or_dash(
                latest_evaluation.get("execution_count")
            ),
        )

    with evaluation_columns[2]:
        st.metric(
            "Plane",
            or_dash(
                latest_evaluation.get("plane")
            ),
        )

    with evaluation_columns[3]:
        st.metric(
            "Policy",
            (
                humanize_policy(
                    latest_evaluation["policy_name"]
                )
                if latest_evaluation.get("policy_name")
                else DASH
            ),
        )

    with evaluation_columns[4]:
        st.metric(
            "Version",
            short_id(
                latest_evaluation.get("version_id")
            ),
        )


# ==========================================================
# Performance History
# ==========================================================

st.divider()

st.subheader(
    "Performance History"
)


if not evaluation_history:

    st.info(
        "No persisted metric history is available yet."
    )


else:

    # Optional policy filter: only shown when at least one
    # evaluation explicitly carries policy metadata.

    known_policy_names = sorted(
        {
            evaluation["policy_name"]
            for evaluation in evaluation_history
            if evaluation.get("policy_name")
        }
    )

    filtered_history = evaluation_history

    if known_policy_names:

        selected_policy = st.selectbox(
            "Policy",
            options=[
                "All",
                *known_policy_names,
            ],
            format_func=lambda value: (
                value
                if value == "All"
                else humanize_policy(value)
            ),
        )

        if selected_policy != "All":

            filtered_history = [
                evaluation
                for evaluation in evaluation_history
                if evaluation.get("policy_name")
                == selected_policy
            ]

    # Metric selector populated from metrics that actually
    # exist in the persisted history.

    metric_names: list[str] = []

    for evaluation in filtered_history:

        for metric in evaluation.get(
            "aggregate_metrics",
            [],
        ):
            metric_name = metric.get("name")

            if (
                isinstance(metric_name, str)
                and metric_name not in metric_names
            ):
                metric_names.append(metric_name)

    if not metric_names:

        st.info(
            "No persisted metric history is available yet."
        )

    else:

        default_metric = (
            "task_fulfillment"
            if "task_fulfillment" in metric_names
            else metric_names[0]
        )

        selected_metric = st.selectbox(
            "Metric",
            options=metric_names,
            index=metric_names.index(
                default_metric
            ),
            format_func=humanize_metric,
        )

        # One point per persisted evaluation, ordered oldest
        # to newest. X labels use explicit version IDs when
        # present; otherwise the evaluation ID. Version
        # associations are never inferred.

        chart_labels: list[str] = []

        chart_values: list[float] = []

        latest_aggregate: dict[str, Any] | None = None

        has_version_labels = False

        for evaluation in filtered_history:

            aggregate = metric_aggregate(
                evaluation,
                selected_metric,
            )

            if aggregate is None:
                continue

            if evaluation.get("version_id"):
                label = str(
                    evaluation["version_id"]
                )

                has_version_labels = True

            else:
                label = str(
                    evaluation["evaluation_id"]
                )

            chart_labels.append(label)

            chart_values.append(
                float(aggregate.get("mean", 0.0))
            )

            latest_aggregate = aggregate

        if latest_aggregate is None:

            st.caption(
                "No persisted values exist for "
                f"{humanize_metric(selected_metric)} yet."
            )

        else:

            if not has_version_labels:
                st.caption(
                    "Version association unavailable for "
                    "historical evaluations; points are "
                    "labeled by evaluation run."
                )

            chart_frame = pd.DataFrame(
                {"Mean": chart_values},
                index=chart_labels,
            )

            chart_frame.index.name = "Evaluation"

            st.line_chart(
                chart_frame,
                height=320,
            )

            st.caption(
                metric_direction(selected_metric)
            )

            st.markdown(
                "##### Latest "
                + humanize_metric(selected_metric)
            )

            detail_columns = st.columns(
                5
            )

            with detail_columns[0]:
                st.metric(
                    "Mean",
                    format_number(
                        latest_aggregate.get("mean")
                    ),
                )

            with detail_columns[1]:
                st.metric(
                    "Median",
                    format_number(
                        latest_aggregate.get("median")
                    ),
                )

            with detail_columns[2]:
                st.metric(
                    "Min",
                    format_number(
                        latest_aggregate.get("minimum")
                    ),
                )

            with detail_columns[3]:
                st.metric(
                    "Max",
                    format_number(
                        latest_aggregate.get("maximum")
                    ),
                )

            with detail_columns[4]:
                st.metric(
                    "Samples",
                    format_number(
                        latest_aggregate.get(
                            "sample_count"
                        )
                    ),
                )


# ==========================================================
# Latest Metric Snapshot
# ==========================================================

if latest_evaluation is not None:

    st.divider()

    st.subheader(
        "Latest Metric Snapshot"
    )

    snapshot_metrics = latest_evaluation.get(
        "aggregate_metrics",
        [],
    )

    if not snapshot_metrics:

        st.caption(
            "No aggregate metrics are available for the "
            "latest evaluation."
        )

    else:

        snapshot = pd.DataFrame(
            {
                "Metric": [
                    humanize_metric(
                        metric.get("name", "?")
                    )
                    for metric in snapshot_metrics
                ],
                "Mean": [
                    format_number(
                        metric.get("mean")
                    )
                    for metric in snapshot_metrics
                ],
                "Median": [
                    format_number(
                        metric.get("median")
                    )
                    for metric in snapshot_metrics
                ],
                "Min": [
                    format_number(
                        metric.get("minimum")
                    )
                    for metric in snapshot_metrics
                ],
                "Max": [
                    format_number(
                        metric.get("maximum")
                    )
                    for metric in snapshot_metrics
                ],
                "Samples": [
                    format_number(
                        metric.get("sample_count")
                    )
                    for metric in snapshot_metrics
                ],
                "Direction": [
                    metric_direction(
                        metric.get("name", "")
                    )
                    for metric in snapshot_metrics
                ],
            }
        )

        st.table(
            snapshot
        )


# ==========================================================
# Evaluation Runs
# ==========================================================

st.divider()

st.subheader(
    "Evaluation Runs"
)

if not evaluation_history:

    st.caption(
        "No evaluation runs recorded."
    )


else:

    runs = pd.DataFrame(
        {
            "Evaluation": [
                or_dash(
                    evaluation.get("evaluation_id")
                )
                for evaluation in evaluation_history
            ],
            "Plane": [
                or_dash(evaluation.get("plane"))
                for evaluation in evaluation_history
            ],
            "Executions": [
                or_dash(
                    evaluation.get("execution_count")
                )
                for evaluation in evaluation_history
            ],
            "Policy": [
                (
                    humanize_policy(
                        evaluation["policy_name"]
                    )
                    if evaluation.get("policy_name")
                    else DASH
                )
                for evaluation in evaluation_history
            ],
            "Version": [
                or_dash(
                    evaluation.get("version_id")
                )
                for evaluation in evaluation_history
            ],
            "Metrics": [
                len(
                    evaluation.get(
                        "aggregate_metrics",
                        [],
                    )
                )
                for evaluation in evaluation_history
            ],
        }
    )

    st.table(
        runs
    )
