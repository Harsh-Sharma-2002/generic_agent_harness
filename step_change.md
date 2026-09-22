# KernelAI — Revised Build Order

> **Purpose:** This file changes only the **implementation order** of KernelAI.
>
> `steps.md` remains the source of truth for the final architecture, scheduling design, research decisions, evaluation plan, and other technical details.
>
> Do **not** remove or reinterpret features from `steps.md` based on this file. This file only changes **when** they are built.

---

## Why the Build Order Is Changing

The existing plan moves relatively quickly toward the final scheduling architecture.

Instead, development will proceed through **working vertical slices**.

The principle is:

> Build the simplest working generic agent harness first. Then introduce concurrency, queueing, checkpointing, scheduling, preemption, and elasticity one layer at a time.

Every stage should produce a runnable system.

This makes it easier to:

* understand each component before adding the next,
* debug failures,
* measure the effect of individual architectural changes,
* avoid designing abstractions for behaviowe have not implemented yet,
* maintain a working product throughout development.

---

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
 └── Agent State
 │
 ▼
Result
```

The worker should accept an arbitrary task and execute a normal agent loop:

```text
Task
 ↓
LLM
 ↓
Tool request?
 ├── No ──► Final answer
 │
 └── Yes
      ↓
   ToolCaller
      ↓
   Tool result
      ↓
     LLM
      ↓
     ...
```

The same `GenericWorker` implementation must be usable for different types of tasks.

### Explicitly NOT part of Stage 0

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

The objective is only to prove:

> **One generic worker can reliably execute arbitrary agent tasks using an LLM and tools.**

### Stage 0 is complete when

A terminal user can submit several different kinds of tasks and the same generic worker can reason, call tools when necessary, consume tool results, continue execution, and return a final answer.

---

# Stage 1 — Multiple Generic Workers

## Goal

Run multiple independent instances of the Stage 0 worker concurrently.

```text
                 Harness
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
     Worker 1    Worker 2    Worker 3
        │           │           │
      Task A      Task B      Task C
```

Use lightweight `asyncio` concurrency initially.

There is still **no scheduler**.

Tasks can initially be directly assigned to workers.

The purpose of this stage is to prove that:

* workers are genuinely generic,
* worker state is isolated,
* several agent loops can execute concurrently,
* one worker failing does not corrupt another.

---

# Stage 2 — Resource and Concurrency Investigation

## Goal

Before designing elastic scaling, determine what resources are actually available and what limits worker concurrency.

Inspect the execution environment from the terminal.

Check things such as:

```text
Cd include:

```bash
uname -a
lscpu
nproc
free -h
ulimit -a
nvidia-smi
```

Also distinguish between **local worker resources** and **remote inference resources**.

When using the ASU-hosted LLM:

```text
GenericWorker
      │
      │ network request
      ▼
ASU LLM Gateway
      │
      ▼
Hosted Model
```

LLM inference is remote.

Therefore local GPU capacity may not determine how many generic workers can exist. Workers may primarily be lightweight asynchronous tasks waiting on network and tool I/O.

The practical limits may instead be:

* remote API concurrency,
* rate limits,
* network latency,
* local memory,
* CPU-heavy tools,
* other external services.

Do not aggressively load-test the shared ASU gateway.

Start with conservative concurrency and increase only through normal project workloads.

The result of this stage should inform later orchestrator design.

---

# Stage 3 — Simple FIFO Worker Pool

## Goal

Introduce scheduling in the simplest possible form.

```text
Incoming Tasks
      │
      ▼
┌──────────────┐
│  FIFO Queue  │
└──────┬───────┘
       │
   ┌───┼───┐
   ▼   ▼   ▼
  W1  W2  W3
```

If every worker is busy, incoming tasks wait.

When a worker becomes available, it receives the oldest waiting task.

There is still:

* no priority,
* no MLFQ,
* no preemption,
* no SJF/SRTF.

This becomes the simple scheduling baseline for later comparison.

---

# Stage 4 — Work Quanta + Checkpoint/Resume

## Goal

Change worker execution from:

```text
run entire task
```

to:

```text
run one safe unit of work
```

Introduce the work-based quantum defined in `steps.md`.

The important distinction remains:

> **A quantum is a unit of work, not a duration of wall-clock time.**

After completing a quantum, task execution must be safely checkpointable.

Required  ...

new worker
 │
 ▼
restore checkpoint
 │
 ├── Quantum 3 ✓
 └── continue
```

Previously completed work must not be repeated.

This stage establishes the primitive required for actual preemption.

---

# Stage 5 — Preemptive Scheduler

## Goal

Replace the simple FIFO scheduler with the scheduling architecture defined in `steps.md`.

This is where we introduce:

* multi-level feedback queues,
* SJF/SRTF ordering,
* work-boundary preemption,
* demotion,
* promotion,
* aging,
* starvation prevention,
* runtime estimation.

Example:

```text
Long Task running
remaining estimate = 30

        ↓

Short Task arrives
estimate = 4

        ↓

Long Task finishes current work quantum

        ↓

checkpoint Long Task

        ↓

run Short Task

        ↓

Short Task finishes

        ↓

resume Long Task
```

Preemption happens at **safe work boundaries**, not arbitrary time boundaries.

The detailed scheduling policy remains defined by `steps.md`.

---

# Stage 6 — Elastic Orchestrator

## Goal

Only after the scheduler and workers function correctly do we introduce the final orchestrator.

Keep the responsibilities separate:

```text
ORCHESTRATOR
How many workers should exist?

SCHEDULER
Which task should run next?
```

Architecture:

```text
                 Orchestrator
                     │
              worker pool size
                     │
         ┌───────────┼───────────┐
         ▼           ▼           ▼
       Worker      Worker      Worker
         ▲           ▲           ▲
         └───────────┼───────────┘
                     │
                 Scheduler
                     │
                   Tasks
```

The resource investigation from Stage 2 should guide the orchestrator's concurrency limits and scaling behavior.

The orchestrator can then scale the generic worker pool according to backlog and available capacity.

---

# After Stage 6

Once the complete runtime works, continue with the remaining major systems already specified in `steps.md`, including:

```text
Observability
      ↓
Offline learning/calibration
      ↓
Evaluation
      ↓
Security / production hardening
```

Their detailed designs remain in `steps.md`.

---

# Development Rule

At any point, work only on the **current stage** unless a small piece of a future stage is strictly necessary for the current one.

In particular:

```text
Stage 0 → Do not design MLFQ.
Stage 1 → Do not design preemption.
Stage 2 → Measure before designing elasticit elasticity only after there is a working scheduler to orchestrate.
```

The target progression is therefore:

```text
Working Agent
     ↓
Concurrent Agents
     ↓
Understand Resources
     ↓
FIFO Runtime
     ↓
Checkpointable Runtime
     ↓
Preemptive Scheduled Runtime
     ↓
Elastic Runtime
```

`steps.md` defines **what KernelAI ultimately becomes**.

`BUILD_ORDER.md` defines **the order in which we get there**.

