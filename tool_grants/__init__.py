"""KernelAI Trusted Domain Tool Grants.

The single source of tool authority:

    TaskState.authorized_domains
            +
    TrustedDomainGrants
            ↓
    allowed_tools

Consumers: Supervisor escalation today, TaskExecutor next.

This package is deterministic and dependency-light: no async, no
network, no MCP, no model, no Second Brain.
"""

from tool_grants.models import (
    DomainGrant,
    ToolAuthorityResolver,
)
from tool_grants.registry import (
    TRUSTED_DOMAIN_GRANTS,
    TrustedDomainGrants,
)


__all__ = [
    "DomainGrant",
    "TRUSTED_DOMAIN_GRANTS",
    "ToolAuthorityResolver",
    "TrustedDomainGrants",
]
