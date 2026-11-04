"""Tests for Trusted Domain Tool Grants.

Assertions use the exact MCP runtime tool identifiers registered in
``mcp_server/server.py``: web_search, sql_executor, shell.
"""

from __future__ import annotations

import pytest

from mcp_server.tools.shell import shell
from mcp_server.tools.sql_executor import sql_executor
from mcp_server.tools.web_search import web_search
from tool_grants.models import DomainGrant
from tool_grants.registry import (
    TRUSTED_DOMAIN_GRANTS,
    TrustedDomainGrants,
)


RUNTIME_TOOL_IDS = frozenset(
    {
        web_search.__name__,
        sql_executor.__name__,
        shell.__name__,
    }
)


@pytest.fixture()
def grants() -> TrustedDomainGrants:
    return TrustedDomainGrants()


def test_grant_table_uses_exact_runtime_tool_ids(
    grants: TrustedDomainGrants,
) -> None:
    """Grant identifiers come from the MCP runtime, not filenames."""

    assert RUNTIME_TOOL_IDS == frozenset(
        {"web_search", "sql_executor", "shell"}
    )

    configured = {
        tool
        for grant in TRUSTED_DOMAIN_GRANTS
        for tool in grant.tools
    }

    assert configured == RUNTIME_TOOL_IDS


@pytest.mark.parametrize(
    ("domain", "tools"),
    [
        ("coding", {"shell"}),
        ("research", {"web_search"}),
        ("data", {"sql_executor"}),
        ("teaching", {"web_search", "shell"}),
    ],
)
def test_known_domain_returns_exact_tools(
    grants: TrustedDomainGrants,
    domain: str,
    tools: set[str],
) -> None:
    result = grants.tools_for_domain(domain)

    assert result == frozenset(tools)
    assert isinstance(result, frozenset)


@pytest.mark.parametrize(
    ("tool", "domains"),
    [
        ("web_search", {"research", "teaching"}),
        ("shell", {"coding", "teaching"}),
        ("sql_executor", {"data"}),
    ],
)
def test_known_tool_returns_exact_trusted_domains(
    grants: TrustedDomainGrants,
    tool: str,
    domains: set[str],
) -> None:
    result = grants.domains_for_tool(tool)

    assert result == frozenset(domains)
    assert isinstance(result, frozenset)


@pytest.mark.parametrize(
    "domain",
    ["does_not_exist", "", "Coding", "code", "software"],
)
def test_unknown_domain_grants_nothing(
    grants: TrustedDomainGrants,
    domain: str,
) -> None:
    assert grants.tools_for_domain(domain) == frozenset()


@pytest.mark.parametrize(
    "tool",
    ["does_not_exist", "", "WEB_SEARCH", "web-search", "browser"],
)
def test_unknown_tool_grants_nothing(
    grants: TrustedDomainGrants,
    tool: str,
) -> None:
    assert grants.domains_for_tool(tool) == frozenset()


def test_capability_policy_matches_the_intended_grants(
    grants: TrustedDomainGrants,
) -> None:
    """coding=shell, research=web, data=sql, teaching=web+shell."""

    assert "shell" in grants.tools_for_domain("coding")
    assert "web_search" in grants.tools_for_domain("research")
    assert "sql_executor" in grants.tools_for_domain("data")

    assert grants.tools_for_domain("teaching") == frozenset(
        {"web_search", "shell"}
    )


def test_reverse_lookup_agrees_with_forward_lookup(
    grants: TrustedDomainGrants,
) -> None:
    """Every domain/tool pair is consistent in both directions."""

    for grant in TRUSTED_DOMAIN_GRANTS:
        for tool in grant.tools:
            assert grant.domain in grants.domains_for_tool(
                tool
            )

            for domain in grants.domains_for_tool(tool):
                assert tool in grants.tools_for_domain(
                    domain
                )

    for grant in TRUSTED_DOMAIN_GRANTS:
        for tool in grants.tools_for_domain(
            grant.domain
        ):
            assert grant.domain in grants.domains_for_tool(
                tool
            )


def test_tool_absent_from_a_domain_is_not_reported(
    grants: TrustedDomainGrants,
) -> None:
    assert "sql_executor" not in grants.tools_for_domain(
        "coding"
    )

    assert "coding" not in grants.domains_for_tool(
        "sql_executor"
    )


def test_returned_collections_are_immutable(
    grants: TrustedDomainGrants,
) -> None:
    tools = grants.tools_for_domain("coding")
    domains = grants.domains_for_tool("shell")

    with pytest.raises(AttributeError):
        tools.add("web_search")  # type: ignore[attr-defined]

    with pytest.raises(AttributeError):
        domains.add("data")  # type: ignore[attr-defined]


def test_registry_exposes_no_mutation_api(
    grants: TrustedDomainGrants,
) -> None:
    for method in (
        "add_grant",
        "remove_grant",
        "set_tools",
        "update_domain",
    ):
        assert not hasattr(grants, method)


def test_empty_registry_grants_nothing() -> None:
    empty = TrustedDomainGrants(
        grants=(),
    )

    assert empty.tools_for_domain("coding") == frozenset()
    assert empty.domains_for_tool("shell") == frozenset()


def test_duplicate_domain_is_rejected() -> None:
    with pytest.raises(ValueError, match="Duplicate trusted grant"):
        TrustedDomainGrants(
            grants=(
                DomainGrant(
                    domain="coding",
                    tools=frozenset({"shell"}),
                ),
                DomainGrant(
                    domain="coding",
                    tools=frozenset({"web_search"}),
                ),
            )
        )


def test_grant_rejects_non_canonical_identifiers() -> None:
    with pytest.raises(ValueError, match="canonical"):
        DomainGrant(
            domain=" coding",
            tools=frozenset({"shell"}),
        )

    with pytest.raises(ValueError, match="canonical"):
        DomainGrant(
            domain="coding",
            tools=frozenset({" shell"}),
        )


def test_grant_coerces_tools_to_frozenset() -> None:
    grant = DomainGrant(
        domain="coding",
        tools={"shell"},  # type: ignore[arg-type]
    )

    assert grant.tools == frozenset({"shell"})
    assert isinstance(grant.tools, frozenset)
