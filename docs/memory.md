# KernelAI Memory Architecture

This document is the authoritative contract for KernelAI's memory layers, their
lifetimes, their relationship to skills, and the boundary between tracked
repository state and local runtime state.

Implementation details may change. These boundaries require an explicit
architecture decision to change.

---

# 1. Repository Boundary

```text
TRACKED REPOSITORY STATE
────────────────────────
init_mem.sh
docs/memory.md
tests/test_memory_init.py

LOCAL RUNTIME STATE
────────────────────
vault/memory/**
```

`vault/memory/` is personal runtime memory and is ignored by Git in its
entirety. No file inside it is tracked, no `.template.md` files live inside it,
and ignored memory files are never force-added.

A fresh clone therefore contains no memory. The initializer creates the
structure locally.

---

# 2. Bootstrap

```text
git clone ...
./init_mem.sh
```

`init_mem.sh` creates any missing local memory infrastructure. It is
idempotent and follows one invariant:

```text
Create missing memory.
Never overwrite existing memory.
```

For example, if `USER.md`, `coding.md` and `data.md` exist while `research.md`
and `teaching.md` are missing, one run creates only `research.md` and
`teaching.md`. Existing files are left byte-for-byte unchanged.

Generated structure:

```text
vault/memory/
├── USER.md
├── domain_profiles/
│   ├── coding.md
│   ├── research.md
│   ├── data.md
│   └── teaching.md
└── sessions/
```

Deliberately not generated:

```text
vault/memory/README.md
vault/memory/domain_profiles/DOMAIN.md
vault/memory/sessions/README.md
STUDENT.md
rollouts/
episodic/
semantic/
vectors/
embeddings/
index.json
memory.db
```

`init_mem.sh` is the canonical source of the live-file schema. Its output is
intentionally thin; there is no second schema maintained in tests or in
hand-written files. Startup may eventually invoke initialization automatically,
but that is future work.

---

# 3. Layers

```text
Session Memory   → generic, live, session-scoped continuity
User Profile     → durable, global, cross-domain user state
Domain Profiles  → durable, user-specific, one work domain
```

## 3.1 Session Memory

Session Memory is generic live context for one ongoing session. It is not
domain-specific, not durable personalization, and not a skill.

```text
Session
├── Request 1
├── Request 2
├── Request 3
└── ...
```

A Session may span many Requests and many domains. Supervisor instances may
remain Request-scoped while Session Memory survives across those Requests.

Because session identity is independent of domain, there are no
`sessions/coding/`, `sessions/research/`, `sessions/data/` or
`sessions/teaching/` directories. The initializer creates only the empty
`sessions/` directory; session files are a future concern.

## 3.2 User Profile

```text
vault/memory/USER.md
```

Durable user-specific state that applies across sessions and across domains:
stable preferences, communication preferences, cross-domain working
preferences, and persistent constraints.

## 3.3 Domain Profiles

```text
vault/memory/domain_profiles/coding.md
vault/memory/domain_profiles/research.md
vault/memory/domain_profiles/data.md
vault/memory/domain_profiles/teaching.md
```

Durable user-specific state within one work domain: how this user tends to work
or learn there.

```text
coding    → coding workflow and code-change preferences
research  → source, retrieval, depth and synthesis preferences
data      → analysis, query and validation preferences
teaching  → teaching preferences and the learner model
```

`teaching.md` also serves as the learner/student model. There is no separate
`STUDENT.md`; the teaching domain profile is the single home for learner state
such as current topic, demonstrated understanding, and recurring
misconceptions.

---

# 4. Session Compaction (future)

Session memory behaves like a managed context window, not a summarizer on a
timer.

```text
live session context
        ↓
continues growing
        ↓
approaches configured usable context threshold
        ↓
compact older context
        ↓
compacted state + recent high-fidelity context
        ↓
continue
```

Compaction is triggered by CONTEXT PRESSURE.

```text
Do NOT summarize after every message.
Do NOT summarize after every Worker call.
Do NOT summarize after every Task.
Do NOT summarize after every Request.
```

The current design target is approximately **90% of usable context capacity**,
not 90% of the raw model context window. Usable capacity accounts for reserved
space such as:

```text
system/runtime instructions
tool definitions
skills
memory projection
current Request
output headroom
safety margin
```

Compaction may run repeatedly during a long session, always preserving recent
context at high fidelity:

```text
raw context
→ compacted_v1 + recent
→ grows
→ compacted_v2 + recent
→ grows
→ compacted_v3 + recent
```

---

# 5. Durable Memory Consolidation (future)

```text
trigger:  future learning/promotion policy
purpose:  learn stable user-specific information and preferences
output:   USER.md or domain_profiles/*
lifetime: across sessions
```

Consolidation is how durable memory is eventually written. No consolidation
policy, candidate extraction, or promotion path exists yet, and no memory
writing tool is exposed to Workers.

---

# 6. Compaction Versus Consolidation

```text
SESSION COMPACTION
────────────────────────
trigger:  context pressure
purpose:  maintain live session continuity
output:   compressed session state
lifetime: current session


DURABLE MEMORY CONSOLIDATION
────────────────────────
trigger:  future learning/promotion policy
purpose:  learn stable user-specific information/preferences
output:   USER.md or domain_profiles/*
lifetime: across sessions
```

These are separate systems. Compaction does not automatically write durable
memory, and durable-memory consolidation does not run merely because
compaction occurred. A long session may be compacted many times while
`USER.md` and the domain profiles stay unchanged.

---

# 7. Authority

Memory is context and personalization, never execution authority.

```text
Memory does not grant domains.
Memory does not grant tools.
Memory does not grant skills.
Memory does not modify TrustedDomainGrants.
```

Future domain-profile routing may conceptually be:

```text
existing Task.authorized_domains
        ↓
MemoryRuntime
        ↓
relevant domain profiles
```

The direction is one-way: existing Task authority determines which memory is
relevant. Memory never produces Task authority.

```text
NEVER:

memory
→ Task authority
```

---

# 8. Relation to Skills

```text
vault/skills/            → generic Worker methodology
vault/supervisor_skills/ → generic Supervisor decomposition methodology
vault/memory/            → user/session-specific state
```

Skills describe how work in a domain is done, for any user. Memory describes
this user and this session. The corpora are never merged: memory is not
reachable as a Worker methodology domain, and loading a skill never reads or
grants memory.

---

# 9. Status

None of the following is implemented:

```text
SessionContext
SessionStore
MemoryRuntime
context injection
session compaction
durable-memory consolidation
memory-writing tools
```

This pass establishes only the bootstrap contract, the tracked architecture
documentation, and tests for the initializer.
