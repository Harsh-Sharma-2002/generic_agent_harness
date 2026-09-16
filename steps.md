# Generic Agent Harness — Phased Plan

## Core idea

A harness that launches multiple Claude-like agent workers and schedules
their tasks the way an OS scheduler schedules processes, specifically a
**Multi-Level Feedback Queue (MLFQ)**, but with the quantum defined in
**units of work** (tool calls / reasoning turns / tokens consumed) instead
of wall-clock time. Every scheduling decision is observable: which queue a
task sits in, why it got preempted, why it got promoted or demoted, and how
long it waited.

This only works if a worker's mid-task state can be paused and resumed
without losing progress, so "preemption" is cheap. That constraint drives
the whole design and is why it comes first below.

## Decisions

- **Worker:** real Claude API calls (via `ANTHROPIC_API_KEY` in a
  gitignored `.env`), not a simulated/mocked LLM loop. The `Worker`
  interface is still written against an abstract base so a simulated
  worker can be swapped in later for fast iteration if benchmark runs get
  too slow/expensive, but real calls are the default from Phase 1 on.
- **Quantum:** counted in tool calls, but the *size* of one quantum (how
  many tool calls a task gets before preemption) is computed dynamically
  at runtime per task/queue-level rather than hardcoded as a fixed
  constant per level. Rationale given: a fixed constant would need
  retuning/recomputing as the task mix or worker behavior changes, so the
  scheduler should derive it from something observed at runtime (e.g. a
  running estimate of that task's typical tool-call cost) instead.
  **This still needs one more concrete decision before Phase 3:** what
  exactly the runtime computation is based on (recent tool-call latency
  for that task? a moving average across the queue level? something
  else?). Flagging this now rather than guessing, since it changes how
  `Quantum` is implemented.
- **Stack:** Python, asyncio.
- **Task source:** synthetic benchmark tasks that we author, so Phase 6's
  evaluation is controlled and repeatable.

## Phase 0 — Scaffold & design doc

- Repo layout: `worker/`, `scheduler/`, `observability/`, `tasks/` (done).
- Define the core abstractions as plain data classes before writing any
  scheduling logic: `Task`, `Worker`, `Quantum`, `Queue`, `SchedulerEvent`.
- No scheduling behavior yet. This phase just fixes vocabulary so Phase 1+
  isn't renaming things halfway through.

## Phase 1 — One worker, one task, checkpointable

- Get a single Claude-like worker to run a task end-to-end.
- Prove the worker's state can be serialized at a quantum boundary, the
  worker process stopped, and the task resumed later from that serialized
  state with no lost progress. This is the load-bearing primitive: if a
  task can't be paused and resumed cheaply, there is no MLFQ, just a
  worker pool with no real preemption.
- No queues, no priority, no concurrency yet. Just: run, pause at a
  quantum boundary, resume, finish.

## Phase 2 — Single queue, round robin, real preemption

- Multiple tasks, one worker, one FIFO queue.
- Scheduler hands the worker the head-of-queue task, lets it run for
  exactly one quantum, then actually preempts it (serialize state,
  re-enqueue at the tail) regardless of whether it finished.
- This is the first point where "quantum" is a real constraint instead of
  a concept on paper. Get this loop rock solid before adding priority.

## Phase 3 — Multi-level feedback queues

- N priority queues, shorter quantum at the top, longer quantum lower
  down (classic MLFQ shape).
- New tasks enter at the top queue.
- A task that burns its full quantum without finishing gets demoted one
  level. A task that yields before its quantum is up (blocked on a tool
  call, waiting on something external) stays at its current level or gets
  promoted, the same distinction a real OS scheduler makes between
  CPU-bound and I/O-bound processes.
- Add aging: a task starved at the bottom queue for too long gets
  promoted back up, so no task waits forever.

## Phase 4 — Multiple concurrent workers

- A real worker pool, not a simulated one. The scheduler assigns
  ready-queue tasks to whichever worker is free.
- Decide and document the worker-pool sizing policy (fixed pool vs.
  scale-on-demand) and what happens when all workers are busy and a
  high-priority task arrives (does it preempt a running low-priority task
  early, or wait for the next quantum boundary?).

## Phase 5 — Observability

- Every scheduling decision emits a structured event: enqueue, dequeue,
  quantum start/end, preemption, demotion, promotion, starvation-aging
  trigger.
- Track per-task metrics: total wait time, number of preemptions, queue
  level over time, turnaround time.
- Export these as traces/dashboards. Reuse the LangSmith-style tracing
  pattern already proven out in Agent Harness if that fits, or build a
  minimal custom exporter if this project needs to stay framework-agnostic.

## Phase 6 — Evaluation (the research-paper payoff)

- Benchmark the work-based MLFQ against a naive baseline (plain FIFO,
  or round robin with no priority levels) on the same task mix.
- Metrics: average turnaround time, average wait time, fairness across
  task types, starvation incidents, and whether the work-based quantum
  (vs. a time-based quantum) changes the results in a way worth writing
  up.
- This phase is where the paper's actual claims get tested, so the task
  mix used for benchmarking should be decided and written down before
  running it, not chosen after seeing which results look best.
