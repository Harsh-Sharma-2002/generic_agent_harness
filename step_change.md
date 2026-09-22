# Stage 0 — Single Generic Agent Harness

## Goal

Build the simplest functional version of KernelAI.

```text
User
 │
 ▼
CLI
 │
 ▼
GenericWorker
 │
 ├── LLMCaller
 ├── ToolCaller
 └── Conversation History
 │
 ▼
Result
```

The worker accepts:

```text
query
request_id
```

and returns:

```text
dict[str, Any]
```

The returned dictionary is intentionally generic so different kinds of tasks can return different result structures.

The worker should accept an arbitrary task and execute a normal agent loop:

```text
query
  │
  ▼
messages
  │
  ▼
 LLMCaller
  │
  ├──── final response ──────────► Result
  │
  └──── tool request
            │
            ▼
        ToolCaller
            │
            ▼
        tool result
            │
            ▼
   append result to messages
            │
            └────────────────────► LLMCaller
```

The same `GenericWorker` implementation must be usable for different types of tasks.

---

## Worker Contract

Stage 0 begins with a deliberately small worker interface:

```python
async def run(
    query: str,
    request_id: str,
) -> dict[str, Any]:
    ...
```

The worker does **not** receive scheduling or orchestration metadata.

Things such as:

```text
task_class
priority
queue level
runtime estimate
worker assignment
arrival time
wait time
preemption count
```

belong to the future runtime/orchestrator execution records, not to the generic worker's task interface.

The worker should not know why or how it was selected to execute a request.

---

## Worker State

Stage 0 does **not** introduce a separate `WorkerState`, planner-state schema, task-state schema, or dynamically generated Python state fields.

The worker's primary task state is its **conversation history**.

For example:

```text
messages
│
├── user query
├── assistant reasoning/action
├── tool request
├── tool result
├── assistant reasoning/action
├── tool request
├── tool result
└── ...
```

Tool results are appended to the conversation and supplied back to the model on the next LLM invocation.

This allows the model to reason about arbitrary task-specific concepts without KernelAI defining Python fields for every possible task.

For example, KernelAI does **not** define fields such as:

```text
needs_discovery
current_route
research_stage
files_remaining
tests_required
needs_validation
```

If a task requires reasoning about these concepts, the model handles them through its context and interaction history.

This keeps the worker genuinely generic.

---

## Minimal Runtime Bookkeeping

A small amount of non-conversation state is allowed when required by the harness itself.

Initially this should be limited to things such as:

```text
request_id
iteration/tool-call count
```

`request_id` exists for correlation and logging.

An iteration/tool-call counter exists only to prevent an agent from entering an infinite execution loop.

These are **runtime bookkeeping**, not task reasoning state.

---

## Planning

Stage 0 does not contain a dedicated `Planner` component or explicit planner-owned state.

Planning is behavior performed by the model inside the normal agent loop.

For example:

```text
User:
Research X, compare A and B, and produce a report.

                 │
                 ▼

               Model
                 │
        internally determines
                 │
       ┌─────────┼─────────┐
       ▼         ▼         ▼
     search    inspect   compare
       │         │         │
       └─────────┴─────────┘
                 │
                 ▼
              answer
```

KernelAI does not need to represent that plan as Python state unless later stages demonstrate a concrete need for it.

A dedicated planning representation may be introduced later if required for checkpointing, preemption, observability, or improved execution quality.

Do not design it during Stage 0.

---

## LLMCaller

`LLMCaller` is responsible only for communication with the configured model backend.

Initially the backend is the ASU Research Computing OpenAI-compatible gateway.

Conceptually:

```text
GenericWorker
      │
      │ messages + available tools
      ▼
   LLMCaller
      │
      ▼
  LLM Provider
      │
      ▼
 model response
```

`LLMCaller` must not know about:

```text
scheduling
MLFQ
priorities
orchestrator state
task classes
worker pools
preemption
```

It is simply the model interface.

---

## ToolCaller

`ToolCaller` executes tool requests produced by the model.

```text
Model
  │
  │ tool request
  ▼
ToolCaller
  │
  ▼
registered tool
  │
  ▼
tool result
  │
  ▼
conversation history
```

The initial tool set should remain small.

The objective of Stage 0 is to validate the generic execution loop, not to build a large tool ecosystem.

---

## Execution Safety

The agent loop must have a hard execution limit.

For example:

```text
MAX_ITERATIONS
```

This prevents:

```text
LLM
 ↓
Tool
 ↓
LLM
 ↓
Tool
 ↓
LLM
 ↓
Tool
 ↓
...
```

from continuing indefinitely.

Reaching the limit should terminate the request cleanly rather than leaving the worker stuck.

---

## Explicitly NOT Part of Stage 0

Do not implement:

* Scheduler
* MLFQ
* SJF/SRTF
* Preemption
* Work quanta
* Checkpoint/resume
* Orchestrator
* Elastic scaling
* A2A
* Offline learning
* Security
* LangSmith scheduling instrumentation
* Dedicated Planner component
* Planner state
* Task-specific state schemas
* Runtime estimation
* Priority metadata

The objective is only to prove:

> **One generic worker can reliably execute arbitrary agent tasks using an LLM, conversation context, and tools.**

---

## Stage 0 Implementation Order

Build Stage 0 in this order:

```text
1. BaseWorker contract
        │
        ▼
2. LLMCaller
        │
        ▼
3. Verify direct asynchronous LLM call
        │
        ▼
4. Tool interface
        │
        ▼
5. ToolCaller / tool registry
        │
        ▼
6. One or two simple tools
        │
        ▼
7. GenericWorker agent loop
        │
        ▼
8. Test direct-answer task
        │
        ▼
9. Test tool-using task
        │
        ▼
10. CLI
        │
        ▼
11. Test unrelated task types
```

Do not move to Stage 1 until this loop works reliably.

---

## Stage 0 Definition of Done

Stage 0 is complete when a terminal user can submit several substantially different kinds of tasks and:

1. The same `GenericWorker` implementation handles all of them.
2. The worker receives only `query` and `request_id`.
3. The worker calls the LLM asynchronously.
4. The model can answer directly when no tool is required.
5. The model can request registered tools.
6. `ToolCaller` executes those tools.
7. Tool results are appended to conversation history.
8. The model can continue execution using those results.
9. Multiple LLM/tool iterations can occur when necessary.
10. The worker eventually returns `dict[str, Any]`.
11. Execution is bounded by a safety limit.
12. No task-specific Python state schema is required.

At this point KernelAI has a functioning **generic agent harness**.

Only then move to Stage 1 and run multiple independent instances of this worker concurrently.
