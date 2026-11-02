# KernelAI Second Brain V1 — Architecture Invariants

This document is the authoritative V1 contract for KernelAI's Second Brain, skill runtime, Task state, tool authority, and escalation model.

Implementation details may change. These boundaries require an explicit architecture decision to change.

---

# 1. Ownership

> **Root owns the system. Supervisor owns the Request and Task authority. Worker owns Task execution.**

```text
Root
    → system lifecycle

Supervisor
    → Request decomposition
    → Task creation
    → domain assignment
    → escalation decisions
    → result aggregation

Worker
    → methodology selection
    → Task execution
    → Task progress
```

Workers cannot expand their own authority.

---

# 2. Skills and Memory Are Separate

> **Skills define HOW to work. Memory defines WHAT the system knows.**

Skills contain methodology, procedures, heuristics, and behavioral instructions.

Memory contains retrievable information and is handled by a separate Brain/memory interface.

Skill loading must not implicitly become memory retrieval.

---

# 3. Vault Is the Only Skill Corpus

```text
vault/skills/
```

is the sole production source of skill methodology and routing information.

The legacy hardcoded skill corpus is retired.

Python may implement skill-runtime infrastructure, but must not maintain a second skill catalog.

---

# 4. Vault Hierarchy

The model-facing hierarchy is:

```text
REGISTRY.md
    ↓
DOMAIN.md
    ↓
<skill-name>.md
```

Responsibilities:

```text
REGISTRY.md
    → Which domain?

DOMAIN.md
    → Which methodologies exist in that domain?

<skill-name>.md
    → How to perform a methodology?

related[]
    → Useful methodology relationships/navigation.
```

---

# 5. Skill Identity

Every skill has a canonical ID:

```text
<domain>.<skill-name>
```

Examples:

```text
coding.systematic-debugging
teaching.progressive-hint-ladder
research.web-search
data.text2sql
```

The runtime resolves canonical IDs to vault files.

The model never supplies arbitrary filesystem paths.

---

# 6. Obsidian Is Visualization, Not Runtime Authority

Obsidian `[[wikilinks]]` exist for:

- human navigation,
- graph visualization,
- documentation.

They have no runtime authority.

Runtime semantics come from canonical metadata and trusted runtime state.

```text
YAML/runtime metadata = machine semantics
Obsidian links        = human projection
```

---

# 7. Supervisor Starts With REGISTRY

At Request start, Supervisor receives:

```text
Supervisor instructions
+
Request
+
REGISTRY.md
```

It does NOT initially receive every:

```text
DOMAIN.md
skill body
tool schema
```

---

# 8. Supervisor Assigns Domains

V1 uses decentralized methodology selection.

Supervisor delegates:

```text
Task instruction
+
authorized domain(s)
```

Example:

```json
{
  "task": "Diagnose why this service intermittently hangs.",
  "domains": ["coding"]
}
```

Supervisor chooses **what work should happen and what domains the Task may access**.

It does not normally choose the Task methodology.

---

# 9. Worker Receives Assigned DOMAINs

At Task start, Worker receives the `DOMAIN.md` for every authorized domain.

Example:

```text
authorized_domains:
    coding
    research

Worker context:
    coding/DOMAIN.md
    research/DOMAIN.md
```

This allows Task-local methodology selection without another Supervisor discovery round.

---

# 10. Domain Grant Gives Full Methodology Access

> **A Worker may load any skill belonging to an authorized domain.**

Example:

```text
authorized_domains = {"coding"}

coding.systematic-debugging       ✓
coding.test-driven-development    ✓
coding.github                     ✓

research.web-search               ✗
```

Individual skills do not require additional Supervisor approval.

---

# 11. `related[]` Is Navigation, Not Authority

`related[]` indicates useful methodology relationships.

It does NOT mean:

- permission,
- dependency,
- prerequisite,
- automatic loading,
- tool grant.

Workers may load an unrelated skill directly when it belongs to an authorized domain.

`related[]` exists to make navigation efficient and meaningful.

---

# 12. One Skill Loading Primitive

Workers use:

```text
load_skill(skill_id)
```

Runtime:

```text
resolve canonical ID
→ verify domain authorization
→ read skill lazily
→ parse/validate
→ project model-relevant content
→ return
```

There are no separate primary/secondary skill-loading APIs.

---

# 13. Skills Are Loaded Lazily

V1 does not parse every full skill at harness startup.

Skills are parsed when requested.

No global parsed-skill cache exists in V1.

Caching may be introduced later if measurements justify it.

---

# 14. Vault Representation ≠ Model Representation

The runtime selectively projects skill content into Worker context.

A loaded skill may expose:

```text
name
description
useful selected metadata
methodology body
related methodologies
```

It should omit model-irrelevant material such as:

```text
licensing/provenance
Obsidian visualization sections
redundant runtime bookkeeping
irrelevant upstream metadata
```

The original vault file remains unchanged.

---

# 15. Skills Are Methodology, Not Security Authority

Editable skill Markdown cannot grant:

- domains,
- tools,
- filesystem authority,
- network authority,
- database authority,
- memory authority.

Loading a skill changes methodology context only.

---

# 16. Skill Limit

A Task may initially load at most:

```text
5 skills
```

across all authorized domains.

The first skill counts.

Duplicate and rejected loads do not consume additional capacity.

---

# 17. Skill-Limit Escalation

Attempting to exceed the skill limit produces Supervisor escalation.

Approved escalation increases the limit by:

```text
+2
```

Therefore:

```text
Attempt 1 → 5
Attempt 2 → 7
Attempt 3 → 9
Attempt 4 → 11
```

Maximum approved escalations:

```text
3
```

Maximum attempts:

```text
4
```

---

# 18. All Authority Expansion Goes Through Supervisor

Worker cannot independently expand:

```text
domains
tools/capabilities
skill limit
```

Relevant escalation kinds include:

```text
DOMAIN_REQUIRED
TOOL_REQUIRED
SKILL_LIMIT_EXCEEDED
```

All are handled by Supervisor.

---

# 19. Escalation Is Structured

Escalation explains why execution cannot continue under the current boundary.

Conceptually:

```text
Escalation
├── task_id
├── attempt
├── kind
├── reason
├── requested_domain?
├── requested_tool?
├── requested_skill_limit?
└── relevant control metadata
```

Supervisor decides:

```text
approve
deny
terminate
```

---

# 20. TaskState Is Persistent

Task execution state lives under:

```python
task_states: dict[str, TaskState]
```

The semantic unit is the Task, not a Worker instance.

Conceptually:

```text
TaskState
├── task_id
├── attempt
├── instruction
├── authorized_domains
│
├── loaded_skills
├── skill_limit
│
├── progress
├── findings
├── artifacts/results
├── remaining_work
│
├── status
└── escalation/blocking state
```

Exact implementation fields may evolve.

---

# 21. Workers Are Disposable; Tasks Persist

Escalation does not create a new semantic Task.

```text
task_123 / attempt 1
        ↓
Worker 1 BLOCKED
        ↓
Supervisor approves
        ↓
attempt += 1
        ↓
Worker 2
        ↓
task_123 / attempt 2
```

Both Workers operate on the same cumulative Task state.

---

# 22. Task Progress Survives Escalation

A new Worker receives enough state to continue rather than restart:

```text
completed work
findings
artifacts/results
remaining work
loaded skills
authorized domains
current limits
```

TaskState must not become an unbounded raw conversation dump or hidden-reasoning archive.

It stores **resumable work state**.

---

# 23. Worker Owns Progress Mutation

Only the active Worker may modify:

```text
progress
findings
artifacts/results
remaining work
loaded skills
methodology state
```

Supervisor must not rewrite Worker progress.

This avoids competing agents corrupting Task execution state.

---

# 24. Supervisor Owns Control Mutation

Supervisor may modify control/authority state during escalation:

```text
authorized domains
skill limit
approved tool/capability expansion
attempt
continue / deny / terminate
```

Supervisor does not rewrite accumulated findings.

---

# 25. Trusted Domain Grants Own Tool Authority

Tool authority comes from trusted runtime configuration:

```text
domain
→ allowed tools
```

Conceptually:

```python
TrustedDomainGrants = {
    "coding": {...},
    "research": {...},
    "data": {...},
    "teaching": {...},
}
```

It does NOT come from:

```text
REGISTRY.md
DOMAIN.md
skill files
related[]
Obsidian links
Worker requests
```

---

# 26. Tool Allocation Is Homogeneous

V1 has no skill-specific tool authority.

The lifecycle is always:

```text
implement tool
    ↓
implement tool guardrails/security
    ↓
add tool to trusted domain grant(s)
    ↓
Supervisor grants domain
    ↓
Task receives corresponding tools
```

No special allocation mechanism exists for individual skills.

---

# 27. Tools Enforce Their Own Security

A domain grant allows invocation of an interface.

It does not imply unrestricted underlying access.

Tools remain responsible for enforcing their own:

```text
sandbox boundaries
filesystem policy
network policy
credential isolation
resource limits
operation restrictions
```

Therefore:

```text
tool granted ≠ unrestricted host authority
```

---

# 28. Tool Authority Is Derived

Where possible:

```text
TaskState.authorized_domains
+
TrustedDomainGrants
        ↓
allowed_tools
```

Do not maintain an independently mutable duplicate tool-authority list.

When Supervisor approves a new domain, allowed tools are recomputed from cumulative domain authority.

---

# 29. Tool Set Is Stable Within an Attempt

A Worker cannot dynamically grant itself new tools during execution.

If a missing capability is required:

```text
Worker
→ Escalation
→ Supervisor
→ approval
→ attempt + 1
→ new Worker
```

This gives each Worker attempt a stable execution environment.

---

# 30. Tool Catalog Stays Outside Model Context

The complete available tool catalog belongs to runtime memory.

Workers receive only the schemas allowed by their current Task authority.

```text
available tools
        ∩
trusted domain grants
        =
Worker tool schemas
```

Tool dispatch must independently verify authorization.

Schema visibility alone is not the security boundary.

---

# 31. Skills and Grants Are Immutable During Production Requests

Production Workers cannot:

```text
rewrite skills
create production skills
change related[]
change trusted grants
```

Production execution treats promoted skills and grants as immutable.

Mutation occurs only through offline/system-management workflows.

---

# 32. Future System Manager Produces Candidates

Future System Manager may analyze production traces and propose:

```text
candidate skill changes
candidate routing changes
candidate domain grants
```

It does not silently mutate production authority.

Conceptually:

```text
production traces
    ↓
System Manager
    ↓
candidate
    ↓
evaluation/security checks
    ↓
human/promotion boundary
    ↓
production
```

---

# 33. Skills and Memory Mutation Stay Outside Workers

Workers may eventually use explicitly granted Brain interfaces.

They do not directly mutate shared system memory or production skill definitions.

Shared-state mutation is coordinated above Task execution.

---

# 34. Attempts Are First-Class in Traces

Every Task event must be attributable to:

```text
request_id
task_id
attempt
```

Worker identity may additionally be recorded.

Offline reconstruction must distinguish multiple attempts of the same semantic Task.

---

# 35. Routing and Escalation Must Be Observable

Traces must allow reconstruction of:

```text
domains assigned
DOMAINs exposed
skills loaded
skill load order
skill-limit usage
tools exposed
tools called
Task progress across attempts
escalation reason
requested expansion
Supervisor decision
Task outcome
```

This data supports both debugging and offline learning.

---

# 36. V1 Is an Explicit Routing Experiment

V1 intentionally uses:

```text
Supervisor → domains
Worker     → skills
```

instead of centralized Supervisor skill selection.

Hypothesis:

> Task-local methodology selection with smaller domain-specific contexts and concurrent Worker routing can improve routing quality and potentially critical-path latency at the cost of higher aggregate inference/token usage.

The runtime should remain measurable enough to compare this against centralized routing using the same Request corpus.

---

# 37. Progressive Disclosure and Security Are Separate

Progressive disclosure answers:

> What information should enter model context?

Security answers:

> What may this Task actually execute?

Therefore:

```text
visible     ≠ authorized
not visible ≠ unauthorized
```

Runtime authority remains authoritative.

---

# 38. No Premature Optimization in V1

Do not add yet:

```text
global skill cache
Neo4j
embeddings/vector search
semantic skill search
learned routing
autonomous cross-domain discovery
skill-specific tool grants
dynamic tool authority
System Manager live mutation
```

Introduce these only when traces demonstrate a concrete need.

---

# Runtime Flow

```text
REQUEST
   ↓
Supervisor
   ├── Request
   └── REGISTRY.md
   ↓
decompose
   ↓
Task + authorized domains
   ↓
TaskState[task_id]
   ↓
TrustedDomainGrants
   ↓
allowed tools
   ↓
Worker
   ├── TaskState
   ├── DOMAIN.md(s)
   └── allowed tool schemas
   ↓
load_skill(skill_id)
   ↓
lazy parse + selective projection
   ↓
execute
   ↓
load additional skills as needed
   ↓
complete
   │
   └── boundary reached
          ↓
       Escalation
          ↓
       Supervisor
          ↓
      approve / deny
          ↓
      same task_id
      attempt + 1
          ↓
       new Worker
```

# Core Invariants

1. **Root owns the system; Supervisor owns Request/authority; Worker owns Task execution.**
2. **Vault is the only skill corpus.**
3. **Supervisor assigns domains; Worker chooses skills.**
4. **An authorized domain gives full methodology access within that domain.**
5. **`related[]` guides navigation; it does not grant authority.**
6. **Skills define methodology, never security authority.**
7. **Workers may load at most five skills initially; expansion requires Supervisor escalation.**
8. **Task progress persists under `task_id`; Workers are replaceable.**
9. **Worker owns progress mutations; Supervisor owns authority/control mutations.**
10. **Trusted domain grants are the sole domain→tool authority mapping.**
11. **Tools enforce their own security boundaries.**
12. **Leaving the current domain/tool/skill-limit boundary requires Supervisor escalation.**
13. **Production skills and grants are immutable during Requests.**
14. **Future System Manager proposes candidates; it does not silently mutate production.**
15. **Every architectural decision remains observable and evaluable.**

> **The Supervisor decides what a Task may access; the Worker decides how to perform it; TaskState preserves the work; trusted grants determine executable interfaces.**

Supervisor Restart Guidance: On an approved escalation, the Supervisor may provide a ≤150-character advisory suggestion to the next Worker. It cannot modify TaskState or grant/override authority.