#!/usr/bin/env bash

set -euo pipefail

# ------------------------------------------------------------
# KernelAI Memory Vault Initializer
#
# Creates the local memory structure used by KernelAI.
#
# IMPORTANT:
# - Memory is local runtime state.
# - Existing memory is NEVER overwritten.
# - Running this script multiple times is safe.
# ------------------------------------------------------------

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEMORY_DIR="$ROOT_DIR/vault/memory"
DOMAIN_DIR="$MEMORY_DIR/domain_profiles"
SESSIONS_DIR="$MEMORY_DIR/sessions"


# ------------------------------------------------------------
# Directories
# ------------------------------------------------------------

mkdir -p "$DOMAIN_DIR"
mkdir -p "$SESSIONS_DIR"


# ------------------------------------------------------------
# Global User Profile
# ------------------------------------------------------------

USER_FILE="$MEMORY_DIR/USER.md"

if [[ ! -f "$USER_FILE" ]]; then
    cat > "$USER_FILE" <<'EOF'
---
name: user-profile
id: memory.user
scope: global
lifetime: durable
---

# User Profile

## Stable preferences

## Communication preferences

## Cross-domain working preferences

## Persistent constraints

## Notes
EOF

    echo "[KernelAI] Created USER.md"
else
    echo "[KernelAI] USER.md already exists; leaving unchanged."
fi


# ------------------------------------------------------------
# Domain Profiles
# ------------------------------------------------------------

create_domain_profile() {
    local domain="$1"
    local file="$DOMAIN_DIR/$domain.md"

    if [[ -f "$file" ]]; then
        echo "[KernelAI] $domain profile already exists; leaving unchanged."
        return
    fi

    case "$domain" in
        coding)
            cat > "$file" <<'EOF'
---
name: coding-profile
id: memory.domain.coding
domain: coding
scope: domain
lifetime: durable
---

# Coding Profile

## Workflow preferences

## Code-change preferences

## Testing and verification preferences

## Architecture preferences

## Review preferences

## Repeated patterns

## Notes
EOF
            ;;

        research)
            cat > "$file" <<'EOF'
---
name: research-profile
id: memory.domain.research
domain: research
scope: domain
lifetime: durable
---

# Research Profile

## Source preferences

## Retrieval preferences

## Depth and stopping preferences

## Synthesis preferences

## Verification preferences

## Repeated patterns

## Notes
EOF
            ;;

        data)
            cat > "$file" <<'EOF'
---
name: data-profile
id: memory.domain.data
domain: data
scope: domain
lifetime: durable
---

# Data Profile

## Analysis preferences

## Query preferences

## Validation preferences

## Presentation preferences

## Repeated patterns

## Notes
EOF
            ;;

        teaching)
            cat > "$file" <<'EOF'
---
name: teaching-profile
id: memory.domain.teaching
domain: teaching
scope: domain
lifetime: durable
---

# Teaching Profile

## Learning preferences

## Current topic state

## Demonstrated understanding

## Recurring misconceptions

## Hint dependence

## Independence and transfer

## Review needs

## Repeated patterns

## Notes
EOF
            ;;

        *)
            echo "[KernelAI] Unknown memory domain: $domain" >&2
            return 1
            ;;
    esac

    echo "[KernelAI] Created $domain profile."
}


create_domain_profile "coding"
create_domain_profile "research"
create_domain_profile "data"
create_domain_profile "teaching"


# ------------------------------------------------------------
# Complete
# ------------------------------------------------------------

echo
echo "[KernelAI] Memory vault initialized."
echo "[KernelAI] Location: $MEMORY_DIR"
