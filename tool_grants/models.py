"""Trusted Domain Grant models.

A grant states which runtime tools one canonical domain makes
available. It carries no skills, methodologies, Tasks, or mutable
authority: those are different concerns and must not become an
authority source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ToolAuthorityResolver(Protocol):
    """
    Read-only domain <-> tool authority.

    Implemented by ``TrustedDomainGrants`` and consumed by both the
    Supervisor escalation path and TaskExecutor. Both sides must use
    the same authority source.
    """

    def tools_for_domain(
        self,
        domain: str,
    ) -> frozenset[str]:
        """Return the tools a trusted domain grants."""
        ...

    def domains_for_tool(
        self,
        tool: str,
    ) -> frozenset[str]:
        """Return the trusted domains able to grant one tool."""
        ...


@dataclass(frozen=True, slots=True)
class DomainGrant:
    """
    One trusted domain's tool authority.

    ``tools`` holds exact runtime capability identifiers — the names
    the MCP runtime exposes. Grants are read-only configuration, so
    the collection is a frozenset and the instance is frozen.
    """

    domain: str
    tools: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(
            self.domain,
            str,
        ):
            raise TypeError(
                "DomainGrant.domain must be a string."
            )

        if (
            not self.domain
            or self.domain != self.domain.strip()
        ):
            raise ValueError(
                "DomainGrant.domain must be a canonical, "
                "non-empty identifier."
            )

        if not isinstance(
            self.tools,
            frozenset,
        ):
            object.__setattr__(
                self,
                "tools",
                frozenset(self.tools),
            )

        for tool in self.tools:
            if not isinstance(
                tool,
                str,
            ):
                raise TypeError(
                    "DomainGrant tools must be strings."
                )

            if not tool or tool != tool.strip():
                raise ValueError(
                    "DomainGrant tools must be canonical, "
                    "non-empty identifiers."
                )
