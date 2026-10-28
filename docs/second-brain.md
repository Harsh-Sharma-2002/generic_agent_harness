# KernelAI Second Brain V1 — Architecture Invariants

These invariants define the boundaries of KernelAI's Second Brain, skill-discovery, and tool-projection architecture.

Implementation details may change. These invariants should not change without an explicit architectural decision.

---

## 1. System Ownership

> **Root manages the system. Supervisor manages the Request. Worker manages the Task.**

- Root handles system-level admission and lifecycle.
- Supervisor owns Request decomposition, Task creation, authority assignment, escalation, and aggregation.
- Worker owns execution strategy within one Task.
- A Worker may refine methodology but may not expand its own authority.

---

## 2. Skills and Memory Are Separate

> **Skills define HOW to work. Memory defines WHAT the system knows.**

Skills contain:

- methodology,
- procedures,
- reasoning strategies,
- behavioral instructions,
- heuristics.

Memory contains retrievable information.

Skill discovery must never implicitly become memory retrieval.

Memory retrieval must use a separate capability/interface.

Workers do not directly mutate shared memory.

---

## 3. Skills Are Methodology, Not Authority

> **A skill file is instructional content and is never itself a security authority.**

Editable Markdown cannot grant:

- tools,
- domains,
- filesystem access,
- network access,
- database access,
- memory-write access,
- or any other executable capability.

A skill may influence how a Worker reasons.

It cannot expand what the Worker is permitted to execute.

---

## 4. Skill Metadata Is Runtime-Indexed

Each skill has a stable canonical ID.

Example:

```text
coding.systematic-debugging
teaching.socratic
research.web-search
data.text2sql
```

The runtime resolves canonical IDs to skill files.

The model never supplies arbitrary filesystem paths for skill loading.

The skill index is runtime infrastructure and is not itself injected wholesale into model context.

---

## 5. YAML `related` Is the Skill Discovery Graph

> **Only structured `related` metadata defines Worker skill-discovery edges.**

Obsidian `[[wikilinks]]` are for:

- human navigation,
- visualization,
- documentation.

They do not create runtime authority or discoverability.

For V1, `related` relationships are intra-domain only.

---

## 6. `related` Is a Soft Relationship

A `related` edge means:

> This methodology may be useful from the current methodology.

It does NOT mean:

- dependency,
- prerequisite,
- automatic import,
- automatic activation,
- tool grant,
- authority grant.

The Worker decides whether following the edge is useful.

The runtime decides whether following it is legal.

---

## 7. Supervisor Controls Initial Skill Authority

The Supervisor assigns the initial skills for a Task.

Assigned skills define the Task's initial methodology and executable authority.

A Task may receive skills from multiple domains.

The Task's authorized domains are derived from its assigned skills rather than maintained as an independent source of truth.

---

## 8. Assigned Skills Outrank Discovered Skills

Instruction precedence is:

```text
Task instruction
    >
assigned skills, in deterministic order
    >
discovered skills
```

A discovered skill supplements the Task.

It does not redefine the Task or become equivalent to a Supervisor-assigned skill.

---

## 9. Worker Skill Discovery Is Bounded Graph Traversal

A Worker may load another skill only when:

1. it is reachable through `related` from an already loaded skill,
2. it belongs to an authorized domain,
3. it is not already loaded,
4. the discovery budget has not been exhausted.

The model chooses which legal edge to follow.

The runtime enforces the graph boundary.

Therefore:

> **Skill discovery is LLM-decided traversal over a runtime-enforced graph.**

---

## 10. Skill Discovery Is Available During Execution

There is no separate mandatory skill-selection/finalization phase.

A Worker starts executing immediately with its assigned methodology and tools.

If execution reveals a methodological gap, it may call `load_skill()` during execution.

Loading a discovered skill changes instructional context only.

It does not change the Task's tool set.

---

## 11. Discovery State Is Task-Local

The runtime tracks, per semantic Task:

```text
assigned skills
loaded skills
discovered skills
discovery count
attempt
escalation count
handoff
```

This state is Request-scoped.

It is not global.

It is destroyed when the Request ends.

---

## 12. Discovery Budget Is Attempt-Aware and Cumulative

Initial discovery limit:

```text
Attempt 1 → 5 discovered skills
```

Each Supervisor-approved escalation increases the total limit by 2:

```text
Attempt 1 → 5
Attempt 2 → 7
Attempt 3 → 9
Attempt 4 → 11
```

Formula:

```text
discovery_limit = 5 + 2 × (attempt - 1)
```

Previously discovered skills remain loaded across attempts.

The discovery count does not reset.

Rejected/invalid skill loads do not consume discovery budget.

---

## 13. Tool Discovery Is NOT Skill Discovery

> **`related` exists only for methodology discovery.**

It has no tool-discovery semantics.

KernelAI does not traverse skill relationships in order to dynamically mutate the Worker tool list.

Skill discovery and tool projection are separate mechanisms.

---

## 14. Skills Do Not Contain Binding Tool Grants

Portable skill files should remain methodology-focused.

The authoritative skill-to-tool relationship lives in trusted runtime configuration.

Conceptually:

```text
skill ID
    ↓
trusted grant map
    ↓
authorized MCP tool IDs
```

This prevents:

- editable Markdown from granting capabilities,
- duplicated tool configuration,
- provider-specific tool names leaking throughout portable skill content.

---

## 15. Trusted Grant Map Is the Tool Authority

The trusted runtime grant map is the sole binding between assigned skills and executable tools.

Conceptually:

```python
SKILL_TOOL_GRANTS = {
    "research.web-search": {
        "kernel_tools.web_search",
    },
    "data.text2sql": {
        "kernel_tools.sql_executor",
    },
}
```

Missing grants default to no executable tools.

The vault cannot modify this authority during production execution.

---

## 16. Tool IDs Are Namespaced

Runtime tool identity must not rely on ambiguous bare names.

Conceptually:

```text
server.tool
```

Examples:

```text
kernel_tools.web_search
kernel_tools.sql_executor
kernel_tools.shell

github.search_code
github.create_pr
```

This prevents tools from different MCP servers from shadowing one another.

---

## 17. MCP Tool Catalog Stays Outside LLM Context

KernelAI may discover a large number of MCP tool definitions.

The complete catalog belongs in runtime memory.

It must not automatically enter Supervisor or Worker context.

Conceptually:

```text
MCP servers
    ↓
list_tools()
    ↓
ToolCatalogSnapshot
    ↓
runtime memory only
```

The existence of a tool in the catalog does not imply that a Task may use or even see it.

---

## 18. Requests Use a Stable Tool-Catalog Snapshot

A Request operates against a stable view of available MCP tools.

The snapshot should be identifiable by a content hash.

The implementation may initially use a Request-scoped catalog or a process-level immutable snapshot pinned by each Request.

The invariant is:

> **Tool schemas must not unpredictably change underneath an active Request.**

Catalog refresh occurs only at a defined boundary.

---

## 19. Tool Authority Is Derived at Attempt Start

For each Task attempt:

```text
assigned skills
    ↓
trusted grant map
    ↓
Task tool authority
```

Tool authority is derived from Supervisor-assigned skills, including skills approved through previous escalations.

Discovered skills do not contribute authority.

---

## 20. Tool Schemas Are Progressively Projected

KernelAI does not expose every MCP schema to every Worker.

Instead:

```text
Task tool authority
        ↓
ToolCatalogSnapshot
        ↓
exact authorized schemas
        ↓
Worker context
```

Therefore a system may know about hundreds of tools while one Worker sees only the few relevant to its assigned capabilities.

> **Tool projection reduces context; it does not create authority.**

---

## 21. Tool Set Is Frozen Within an Attempt

Once a Task attempt begins:

> **Its executable tool set does not change.**

Worker skill discovery cannot mutate it.

If additional capability becomes necessary, the Worker must escalate.

This gives each attempt a stable execution environment.

---

## 22. Discovered Skills Add Instructions Only

Suppose a Worker discovers:

```text
coding.test-driven-development
```

Loading it may add TDD methodology to the Worker context.

It does NOT automatically:

- add shell,
- add filesystem write,
- add testing tools,
- modify tool schemas,
- widen Task authority.

If the Worker can apply the methodology using its existing tools, it may continue.

If it genuinely requires unavailable capability, it escalates.

---

## 23. Tool Availability Is Enforced at Dispatch

Hiding unauthorized tool schemas is not the security boundary.

The runtime must enforce:

```text
requested tool ∈ Task tool authority
```

when executing the call.

Therefore even if a model hallucinates or somehow references another tool, the runtime rejects it.

---

## 24. Capability Expansion Requires Supervisor Approval

If a Worker requires:

- another domain,
- another Supervisor-assigned skill,
- another executable capability,

it cannot grant that authority to itself.

It returns a structured `BLOCKED` outcome.

The Supervisor decides whether to approve the expansion.

---

## 25. BLOCKED Is Recoverable

A blocked Task returns structured information such as:

```text
kind:
    capability
    methodology
    discovery_budget_exhausted

need:
    free-text explanation

requested_skill:
    optional

requested_domain:
    optional

handoff:
    bounded partial state
```

The Worker is not required to know a canonical skill ID it has never been shown.

---

## 26. Configuration Failure Is Not Capability Escalation

Examples:

- trusted grant references nonexistent MCP tool,
- malformed skill metadata,
- broken runtime mapping,
- invalid tool catalog entry.

These are configuration/runtime failures.

They must not consume Supervisor escalation budget.

The Supervisor cannot fix broken infrastructure by granting more authority.

---

## 27. Semantic Task Identity Survives Escalation

Supervisor-approved escalation retains the same:

```text
task_id
```

and increments:

```text
attempt
```

Example:

```text
task_123 / attempt 1
task_123 / attempt 2
task_123 / attempt 3
```

`task_id` identifies the semantic work.

`attempt` identifies an execution under a particular authority boundary.

---

## 28. Authority Accumulates Across Approved Attempts

When the Supervisor approves additional capability:

```text
previous assigned skills
+
new approved skills
```

become the assigned set for the next attempt.

Tool authority is recomputed from that cumulative assigned set.

Previously approved capabilities are retained.

This prevents repeated escalation for capabilities already needed earlier in the Task.

---

## 29. Maximum Three Approved Escalations

One semantic Task may receive at most:

```text
3 Supervisor-approved escalations
```

Therefore:

```text
maximum attempts = 4
```

Denied escalation does not consume the approved-escalation count.

Once the limit is exhausted, another BLOCKED result becomes terminal for that semantic Task.

---

## 30. Handoff Preserves Work, Not Hidden Reasoning

When a Task continues into another attempt, the runtime preserves a bounded handoff containing results such as:

```text
completed work
facts/findings
remaining work
blocking need
```

The handoff must not become a dump of the Worker's complete context or private reasoning.

The runtime owns the preserved handoff.

The Supervisor may approve new authority but must not rewrite previous Worker state.

---

## 31. Discovery State Survives Escalation

Across attempts of the same semantic Task, preserve:

```text
loaded skills
discovered skills
discovery count
relevant discovery state
handoff
```

This prevents the replacement Worker from spending iterations rediscovering methodologies already used by its predecessor.

Discovered skills remain discovered; escalation does not automatically promote them to assigned status.

---

## 32. Attempts Must Be First-Class in Traces

Every Task-scoped runtime event must be attributable to:

```text
(task_id, attempt)
```

Offline reconstruction must not group only by `task_id`.

Otherwise multiple attempts of one semantic Task become indistinguishable.

---

## 33. Skill and Tool Versions Must Be Reconstructable

Runtime traces should identify the artifacts actually used through hashes/version identifiers.

Relevant data includes:

```text
skill content hashes
domain/index hashes
tool catalog snapshot hash
exposed tool schema hashes
```

The trace does not need to duplicate complete skill bodies or schemas.

---

## 34. Production Skills Are Immutable During Requests

Skill mutation and normal Request handling do not overlap.

Production execution treats the skill corpus as immutable.

Skill modifications happen in the offline phase.

After promotion, runtime indexes/catalogs are refreshed at a defined boundary.

---

## 35. Workers Cannot Mutate Skills

Production Workers may:

- read assigned skills,
- discover allowed related skills,
- execute Tasks.

They may not:

- rewrite skills,
- create production skills,
- change relationships,
- change trusted grants.

Skill evolution belongs to the offline learning/management boundary.

---

## 36. Workers Cannot Mutate Shared Memory

Workers may eventually read memory through explicitly granted Brain capabilities.

They do not independently write shared memory.

Memory mutation is coordinated above the Task level after Task results are available.

This prevents parallel Workers from racing over shared knowledge.

---

## 37. Observability Must Preserve Decision Boundaries

The runtime must make it possible to reconstruct:

```text
what domains/skills were available
what skills were assigned
what skills were discovered
which related edge was followed
which tools were exposed
which tools were actually called
what attempt executed them
why a Task became BLOCKED
what escalation was requested
whether it was approved
```

Observability should record decisions and artifact identities, not duplicate entire prompts unnecessarily.

---

## 38. Progressive Disclosure Is About Context, Not Security

Progressive disclosure answers:

> What information should enter the model context now?

Security answers:

> What is this Task actually authorized to execute?

These must remain separate.

Therefore:

```text
not visible
≠
not authorized

visible
≠
authorized
```

Runtime enforcement remains authoritative.

---

## 39. Skill Discovery and Tool Projection Have Different Control Models

Skill discovery requires semantic reasoning:

```text
LLM
→ choose methodology
```

Tool projection does not:

```text
runtime
→ derive schemas from assigned authority
```

Therefore KernelAI should not spend LLM iterations searching the tool catalog when deterministic skill-derived projection is sufficient.

LLM-driven tool search remains a possible future fallback if production traces show that skill-derived authority cannot predict necessary tools.

---

## 40. Filesystem Organization Does Not Define Runtime Hierarchy

Imported skill packs may contain arbitrary organizational subdirectories.

Those directories do not automatically become agent routing levels.

Runtime hierarchy is defined by explicit metadata/indexes.

Therefore an imported structure such as:

```text
coding/
└── software-development/
    └── debugging/
        └── systematic-debugging/
            └── SKILL.md
```

does not imply:

```text
coding
→ software-development
→ debugging
→ systematic-debugging
```

unless KernelAI explicitly defines those as routing levels.

---

## 41. Standard Skill Bundles Should Remain Portable

Where practical, imported skills should retain the standard bundle form:

```text
skill-name/
├── SKILL.md
├── references/
├── scripts/
└── assets/
```

KernelAI-specific routing/security infrastructure should wrap these bundles rather than unnecessarily rewriting their methodology.

This keeps the skill corpus reusable across compatible agent runtimes.

---

## 42. No Global Routing Cache in V1

Skill-routing caches are deferred.

V1 favors deterministic context construction and reproducible routing.

Caching may be added later if traces demonstrate repeated routing/discovery cost is materially significant.

---

## 43. V1 Must Remain Measurable

The purpose of the POC is not merely to demonstrate that the architecture runs.

It should let us measure:

- routing accuracy,
- skill-discovery usefulness,
- unnecessary skill loading,
- exposed tool-schema token count,
- exposed-but-unused tools,
- tool-selection failures,
- capability escalations,
- methodology escalations,
- duplicate work across attempts,
- effectiveness of handoffs,
- context growth,
- latency added by discovery.

Future architecture changes should be driven by these traces rather than hypothetical scale problems.

---

# Core Summary

The architecture can be reduced to five fundamental invariants:

### 1. Supervisor controls authority.

```text
Supervisor
→ assigned skills
→ Task authority
```

### 2. Worker controls methodology inside that authority.

```text
assigned skills
→ related discovery
→ additional instructions
```

### 3. Discovery never creates authority.

```text
discover skill
≠
gain tools
≠
gain domain
```

### 4. Tools are deterministically projected from approved authority.

```text
assigned skills
→ trusted grant map
→ tool catalog
→ exact schemas
```

### 5. Leaving the authority boundary requires escalation.

```text
Worker needs more authority
→ BLOCKED
→ Supervisor
→ approve / deny
→ new attempt
```

Or, in one line:

> **The Supervisor defines what a Task may do; the Worker decides how to do it; the runtime determines what concrete interfaces that authority exposes.**