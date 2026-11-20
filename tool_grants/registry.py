"""Trusted production domain -> tool authority.

The identifiers below are the MCP runtime's tool names, registered in
``mcp_server/server.py`` from the callables in ``mcp_server/tools/``:

    web_search
    sql_executor
    shell

A tool id is an authority boundary, so it must match the runtime
exactly. There is no alias table, no fuzzy matching, and no fallback
between domains.

This module is read-only system configuration. Nothing in the runtime
— Worker, Supervisor, Task, TaskState, skill, DOMAIN.md, REGISTRY.md,
Second Brain, or an MCP tool result — can change a grant.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Iterable, Mapping

from tool_grants.models import DomainGrant


#: The trusted V1 grant table.
#:
#: V1 keeps tool authority homogeneous: a domain grants tools, and a
#: skill or methodology never does.
TRUSTED_DOMAIN_GRANTS: tuple[DomainGrant, ...] = (
    # Terminal / shell capability.
    DomainGrant(
        domain="coding",
        tools=frozenset({"shell"}),
    ),
    # Web search capability.
    DomainGrant(
        domain="research",
        tools=frozenset({"web_search"}),
    ),
    # SQL / database execution capability.
    DomainGrant(
        domain="data",
        tools=frozenset({"sql_executor"}),
    ),
    # Web search + terminal / shell capability.
    DomainGrant(
        domain="teaching",
        tools=frozenset({"web_search", "shell"}),
    ),
)


class TrustedDomainGrants:
    """
    Deterministic domain <-> tool authority lookups.

    Implements the Supervisor's ``ToolAuthorityResolver`` seam. The
    same instance shape will derive Worker tools when TaskExecutor is
    migrated, so both sides share one authority source.

    There are deliberately no mutation methods: this is the authority
    boundary, not a policy editor.
    """

    def __init__(
        self,
        grants: Iterable[DomainGrant] | None = None,
    ) -> None:
        selected = (
            TRUSTED_DOMAIN_GRANTS
            if grants is None
            else tuple(grants)
        )

        by_domain: dict[str, frozenset[str]] = {}

        for grant in selected:
            if not isinstance(
                grant,
                DomainGrant,
            ):
                raise TypeError(
                    "TrustedDomainGrants expects DomainGrant "
                    "entries."
                )

            if grant.domain in by_domain:
                raise ValueError(
                    "Duplicate trusted grant for domain "
                    f"{grant.domain!r}."
                )

            by_domain[grant.domain] = grant.tools

        by_tool: dict[str, set[str]] = {}

        for domain, tools in by_domain.items():
            for tool in tools:
                by_tool.setdefault(
                    tool,
                    set(),
                ).add(domain)

        self._by_domain: Mapping[
            str,
            frozenset[str],
        ] = MappingProxyType(by_domain)

        self._by_tool: Mapping[
            str,
            frozenset[str],
        ] = MappingProxyType(
            {
                tool: frozenset(domains)
                for tool, domains in by_tool.items()
            }
        )

    def tools_for_domain(
        self,
        domain: str,
    ) -> frozenset[str]:
        """
        Return the tools one trusted domain grants.

        An unknown domain grants nothing: absence of a grant is
        represented as an empty frozenset, never an error and never a
        fallback.
        """

        return self._by_domain.get(
            domain,
            frozenset(),
        )

    def domains_for_tool(
        self,
        tool: str,
    ) -> frozenset[str]:
        """
        Return every trusted domain able to grant one tool.

        Empty means no configured domain grants the tool, so no
        approval can manufacture that authority.
        """

        return self._by_tool.get(
            tool,
            frozenset(),
        )
