---
# AGENT SKILLS STANDARD FIELDS (v2)
name: context-allocation
description: "Context methodology for carrying historical Session information into Tasks."

# KERNELAI CANONICAL FIELDS
id: supervisor.context-allocation
related: []

# UPSTREAM / ADDITIONAL FIELDS
domain: supervisor
---

# Context Allocation

This methodology governs how historical Session information reaches current
work. It does not describe a work domain and does not propose Task boundaries.

## What this methodology answers

Domain decomposition methodologies — `supervisor.coding`, `supervisor.research`,
`supervisor.data`, `supervisor.teaching` — answer one question:

> How should work in this domain be decomposed into Tasks?

Context allocation answers a different question:

> What historical Session information should be carried forward so the
> Supervisor can understand the current Request and create self-contained
> Tasks?

A Session is the user-facing conversation. One Session contains many Requests.
Supervisor instances remain Request-scoped while Session Memory survives across
them.

```text
Session
├── Request 1
├── Request 2
└── Request 3 ← current
```

The current Request may depend on any earlier Request. Resolving that
dependency is what context allocation does.

## V1 behavior

V1 is intentionally simple. There is no retrieval step, no memory search tool,
and no context-allocation runtime tool.

```text
Session history available
        ↓
Supervisor sees the full current Session Context
        ↓
Supervisor identifies historical information relevant to the current Request
        ↓
Supervisor includes the required historical information in Task instructions
        ↓
Worker executes a self-contained Task
```

The Supervisor does this by reading the context it already holds. It does not
call a tool to search memory, and no component retrieves history on its behalf.

### References that require resolution

A current Request may point at earlier conversation instead of restating it:

- "compare that with AMD"
- "continue what we were doing"
- "use the approach from earlier"
- "test me again on what I got wrong"
- "apply the same fix to the other module"

These are examples of the phenomenon, not runtime rules. When a Request contains
such a reference, the Supervisor resolves it from Session Context before writing
the Task instruction. A Worker is never asked to resolve it.

## Worker boundary

Workers do not receive the Session Context.

```text
Session Context
      ↓
Supervisor
      ↓
Task decomposition + context allocation
      ↓
self-contained Task instruction
      ↓
Worker
```

A Task must carry enough relevant history for its Worker to execute
independently. Do not require a Worker to search Session Memory, load
conversation history, retrieve previous Requests, or interpret references such
as "that thing from earlier". The Supervisor resolves those before delegation.

## Why allocation belongs to the Supervisor

Giving every Worker the whole Session multiplies the same context by the number
of parallel Tasks:

```text
large Session
×
many parallel Workers
→ repeated irrelevant context
→ large token cost
→ increased latency
→ weaker task isolation
```

Allocating once, in the Supervisor, produces the opposite shape:

```text
large Session
→ Supervisor once
→ small self-contained Task instructions
→ bounded Worker contexts
```

This preserves the existing Task abstraction: a Task is a meaningful,
independently executable unit of work, not a copy of the conversation.

## What may need to be carried forward

The Supervisor identifies the historical information the current Request
actually depends on. Categories worth checking:

- previous user requirements and constraints
- previous decisions and the reasons for them
- previous assistant conclusions the user is building on
- previous Task results
- useful external tool evidence
- unresolved or incomplete work
- important constraints stated earlier
- references in the current message to earlier conversation

This list guides attention. It is not a checklist to copy wholesale.

## Context minimization

Even though V1 gives the Supervisor the full Session Context, the Supervisor
does not copy the Session into Tasks. Allocate the minimum historical context
required for the Task to be self-contained.

Preserve:

- facts the Task needs
- decisions the Task needs
- constraints the Task needs
- relevant evidence and results
- provenance when it affects how the evidence should be trusted

Do not propagate:

- unrelated conversation
- unrelated tool results
- obsolete intermediate reasoning
- runtime bookkeeping
- details of other Tasks that do not affect this one

Carrying less is correct whenever the Task still stands alone.

## Task abstraction remains authoritative

Context allocation enriches a Task instruction. It does not change what a Task
is.

```text
Task
→ bounded semantic objective
→ authorized domains
→ self-contained instruction
→ disposable Worker
```

Decomposition still decides how many Tasks exist and what each one asks for.
Allocation only decides which history accompanies each instruction.

## Authority invariant

Session Memory and allocated context never grant domains, tools, skills, or
skill capacity.

```text
Session says:
"We used web_search earlier."

Current Task authorized_domains = {"coding"}

→ the Worker does not gain research or web_search authority.
```

Context is information, not authority. If a current Task genuinely needs a
capability used earlier, that is a domain decision made through the normal
delegation and escalation boundary. History does not supply it.

## Tool evidence

Session Memory may retain useful external evidence: web search results,
database query results, shell or file observations, and future external tool
results. When such evidence matters to a new Task, the Supervisor may carry the
useful information into the Task instruction.

Do not copy every historical raw tool result. Prefer distilled, relevant
evidence unless exact raw content is what the Task needs.

## What Session Memory may contain

The working direction is that Session Memory may contain user messages,
assistant final messages, useful external tool calls and results, and possibly
Worker Task results. Whether Task results are persisted is not settled here.

Session Memory does not contain internal chain-of-thought, Supervisor hidden
reasoning, Worker hidden reasoning, runtime trace events, budget events,
`get_skill` results, `get_supervisor_skill` results, or `delegate_task`
bookkeeping.

## Relationship to Session Compaction

Context allocation is not Session Compaction.

```text
SESSION COMPACTION
trigger:  context pressure
purpose:  compress older Session Context


CONTEXT ALLOCATION
input:    available Session Context + current Request
purpose:  determine what history matters for current work
```

V1 allocates from whatever Session Context exists: full, uncompacted, or already
compacted. Compaction changes how much history is available. Allocation decides
which part of it enters a Task.

## Future retrieval

When Sessions become very large, full Session Context will not fit. The intended
evolution is:

```text
Session grows very large
        ↓
recent raw context + compacted context + historical session store
        ↓
future hybrid retrieval
        ↓
relevant historical context
        ↓
Supervisor
```

Retrieval may eventually combine semantic similarity, lexical or full-text
similarity, recency, and metadata. No algorithm, dependency, index, embedding,
or retrieval mechanism is chosen here or implemented now.

## Staging note

Context retrieval logically happens before the Supervisor can fully reason about
the current Request. This methodology therefore does not claim that
`get_supervisor_skill` solves historical-context retrieval: that tool loads
planning guidance, and it runs only after the Supervisor has already begun.

A future runtime may need a pre-Supervisor Context Manager or Context Allocator:

```text
SessionStore
      ↓
Context Manager
      ↓
Supervisor Context
      ↓
Supervisor
      ↓
domain decomposition methodologies
      ↓
Tasks
```

None of that exists yet. Until it does, this file is guidance the Supervisor
applies to context it already holds.

## Scope discipline

- Do not expand a Task's requirements to justify carrying more history.
- Do not carry history the Task does not need merely because it is available.
- Do not restate the Session in a Task instruction; carry what the Task needs
  and nothing else.
- Historical context never becomes a requirement the user did not make.

This methodology allocates context. It grants no domain, execution tool, or
Worker skill.
