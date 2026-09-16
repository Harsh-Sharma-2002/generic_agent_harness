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

**Three layers, not two.** Earlier phases implied Scheduler → Workers.
There's actually a third layer above the scheduler:

- **Orchestrator/planner:** decides *how many* live workers exist right
  now, scaling that pool up or down elastically based on available
  resources (analogous to a Kubernetes HPA, not something this repo needs
  Kubernetes for, just the same control-loop idea). Owns the worker pool.
- **Scheduler (MLFQ):** given whatever pool the orchestrator currently
  maintains, decides *which* ready task each free worker picks up next,
  and enforces quantum/preemption/priority. Unchanged from Phases 2-3.
- **Worker:** a *template*, not a fixed implementation. `generic_agent_harness`
  ships a `BaseWorker` (or similarly named abstract class) defining the
  agent-loop shape (reasoning turn → tool call → tool result →
  checkpoint-at-quantum-boundary → next turn) plus the hooks the scheduler
  and orchestrator need (`run_quantum()`, `checkpoint()`, `resume()`,
  `is_done()`). A concrete task type subclasses it. This is what makes the
  repo a *generic* harness rather than a harness for one specific agent.

## Decisions

- **Worker, two-tier:** real LLM calls, not a simulated/mocked loop, split
  across a live path and an offline path rather than one fixed backend.
  - *Live path:* every task the scheduler actually runs during the day
    goes through ASU Research Computing's free OpenAI-compatible gateway
    (`ASU_LLM_BASE_URL=https://openai.rc.asu.edu/v1`, key in a gitignored
    `.env` as `ASU_LLM_API_KEY`). Chosen because it's instant and
    always-on, so the scheduler's own event loop never blocks on a Slurm
    queue. **Model: `glm-5-2`** (ASU catalog tags it for tool-driven agent
    loops, LiveBench 82.5, 131K context).
  - *Offline path:* once a day, a Slurm batch job on Sol spins up
    **Qwen2.5-72B-Instruct** (the strongest model in ASU's vLLM table,
    requiring `4×80G A100s` per
    [ASU's docs](https://docs.rc.asu.edu/vllm) — picked for judge/learner
    quality, hardware need accepted as a cost of that choice), reachable
    only via SSH tunnel for the life of that job. This model is *not* in
    the live request path; it does two things once a day, then the job
    ends:
    1. Acts as **LLM-as-judge** over a sample of the day's completed
       tasks (Phase 6), scoring output quality with a stronger model than
       whatever's answering live requests, so the judge isn't grading its
       own homework.
    2. Is the **offline learner** that updates the dynamic quantum-size
       estimate (see Quantum below), by reviewing the day's LangSmith
       traces and producing a new estimate for the live scheduler to read.
  - "Claude-like worker" describes the agent-loop *shape* (reasoning turn
    → tool call → tool result → next turn, same structure documented for
    the Claude/Codex loop in the lecture deck), not a specific model or
    backend — both the live and offline paths implement the same `Worker`
    interface.
- **Quantum:** counted in tool calls. The *size* of one quantum (how many
  tool calls a task gets before preemption) is **not** computed fresh on
  every request — that would put an expensive computation in the live
  request path, which contradicts using the gateway for speed in the
  first place. Instead: the live scheduler reads a per-queue-level
  estimate that was last updated by the offline Sol job, and only
  recomputes between daily runs. **This is my inference connecting the
  "computed at runtime" answer to the "learn once a day" answer** — flag
  it if that's not what was meant, since it's the single biggest driver
  of how `Quantum` and the offline job's interface get built.
- **Observability:** LangSmith tracing on every scheduling event and every
  worker LLM call (reusing the pattern already proven in Agent Harness),
  both to power the Phase 5 dashboards and as the raw data the offline
  Sol job reads to update the quantum estimate and run LLM-as-judge scoring.
- **Stack:** Python, asyncio.
- **Task source:** synthetic benchmark tasks that we author, so Phase 6's
  evaluation is controlled and repeatable.
- **Task submission:** HTTP API, not an in-process Python call. Chosen
  deliberately over the simpler option because the goal is to keep this
  close to how it would run in production, and because a future
  containerized deployment would end up talking HTTP anyway, so building
  that boundary in from Phase 1 avoids a rewrite later. **Framework
  pick (not yet confirmed by you): FastAPI** — async-native, matches the
  asyncio stack, and the pool of currently-live workers can be exposed as
  live state on the same app rather than a second service.
- **Orchestrator scaling policy:** driven by backlog, not by error/latency
  signals. Reasoning given: once the system is already resource-saturated,
  launching more workers doesn't help, so error rate isn't a useful
  scale-*up* signal, only a scale-*down*/ceiling one, and the ceiling is
  simpler to just set directly. Concretely: active worker coroutines track
  the number of pending requests, up to a **configured max concurrency**
  (a number reflecting known/assumed gateway capacity, not yet picked —
  see open item in Phase 4), and scale back down as workers finish and
  the queue empties.
- **Scaled instance = asyncio coroutine** within one process (not a
  separate OS process or node).
- **Scale-down behavior = graceful drain**: a worker being scaled down
  finishes its current quantum, then isn't given another task; no
  mid-quantum hard preemption for this specific case.

## Phase 0 — Scaffold & design doc

- Repo layout: `worker/`, `scheduler/`, `observability/`, `tasks/`,
  `api/` (the HTTP submission layer).
- Define the core abstractions as plain data classes before writing any
  scheduling logic: `Task`, `Worker` (as the subclassable template
  described above), `Quantum`, `Queue`, `SchedulerEvent`.
- No scheduling behavior yet. This phase just fixes vocabulary so Phase 1+
  isn't renaming things halfway through.

## Phase 1 — One worker, one task, checkpointable

- A minimal HTTP endpoint (`POST /tasks`) accepts the one task, since
  that's the standing decision for how tasks enter the system, not a
  Python-script shortcut to be rewritten later.
- Get a single Claude-like worker (a first concrete subclass of the
  `BaseWorker` template) to run that task end-to-end.
- Prove the worker's state can be serialized at a quantum boundary, the
  worker process stopped, and the task resumed later from that serialized
  state with no lost progress. This is the load-bearing primitive: if a
  task can't be paused and resumed cheaply, there is no MLFQ, just a
  worker pool with no real preemption.
- No queues, no priority, no concurrency, no orchestrator yet. Just:
  submit over HTTP, run, pause at a quantum boundary, resume, finish.

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

## Phase 4 — Orchestrator: elastic worker pool

- A real worker pool, not a simulated one, but the pool size is no longer
  fixed. The orchestrator/planner layer scales the number of live worker
  coroutines to track pending-request backlog, up to a configured max
  concurrency, and drains back down as the queue empties (see Orchestrator
  scaling policy in Decisions). The scheduler still decides which ready
  task each currently-live worker picks up.
- **Still open: the actual max-concurrency number.** This needs to
  reflect real capacity of the ASU gateway (undocumented rate limits, so
  probably needs empirical discovery, e.g. ramping concurrency in a test
  script until errors appear, similar to how `cold_email`'s pacing ramp
  found a safe sending rate) rather than being picked arbitrarily.
- Also still open: what happens when the pool is already at max
  concurrency and a high-priority task arrives — does it preempt a
  running low-priority task early, or just wait for the next free/drained
  worker like everything else?

## Phase 5 — Observability

- Every scheduling decision emits a structured event: enqueue, dequeue,
  quantum start/end, preemption, demotion, promotion, starvation-aging
  trigger.
- Track per-task metrics: total wait time, number of preemptions, queue
  level over time, turnaround time.
- Export these as LangSmith traces, both for the live scheduler dashboard
  and as the dataset the Phase 7 offline job reads.

## Phase 6 — Evaluation (the research-paper payoff)

- Benchmark the work-based MLFQ against a naive baseline (plain FIFO,
  or round robin with no priority levels) on the same task mix.
- Scheduling metrics: average turnaround time, average wait time,
  fairness across task types, starvation incidents, and whether the
  work-based quantum (vs. a time-based quantum) changes the results in a
  way worth writing up.
- Output-quality metric: LLM-as-judge, using the offline Sol model (not
  the live gateway model) to score a sample of task outputs, so schedule
  changes that speed things up but degrade output quality actually show
  up in the results.
- This phase is where the paper's actual claims get tested, so the task
  mix used for benchmarking should be decided and written down before
  running it, not chosen after seeing which results look best.

## Phase 7 — Offline daily learning loop (Sol)

- A Slurm batch job on Sol, run once a day, hosting Qwen2.5-72B-Instruct
  via vLLM (`4×80G A100s`, per
  [ASU's docs](https://docs.rc.asu.edu/vllm)) for the duration of that
  job only. Reached via SSH tunnel, not a persistent endpoint. A 4×80G
  request on a shared cluster may queue behind other jobs, so this phase
  should also decide what the live scheduler does if a given day's
  offline run doesn't complete in time (keep using yesterday's quantum
  estimate is the obvious default, but write it down rather than leaving
  it implicit).
- Reads the day's LangSmith traces (Phase 5) and produces an updated
  per-queue-level quantum-size estimate for the live scheduler to read on
  its next run (closes the Quantum decision above).
- Also runs the LLM-as-judge scoring pass used in Phase 6.
- Depends on Phase 5 existing (needs real trace data to learn from) and
  is really only worth building once Phase 3's dynamic quantum sizing is
  in place to consume its output — so this phase's own code can start
  early, but it has nothing to plug into until then.
