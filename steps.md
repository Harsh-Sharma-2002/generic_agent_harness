# Generic Agent Harness — Phased Plan

## Core idea

A harness that launches multiple Claude-like agent workers and schedules
their tasks the way an OS scheduler schedules processes, specifically a
**Multi-Level Feedback Queue (MLFQ)**. Two axes, kept deliberately
separate:

- **What a step/quantum *is*:** a unit of work (tool calls), not
  wall-clock time. This is the preemption boundary and is immune to
  per-call latency noise.
- **What SJF orders tasks *by*:** a wall-clock time estimate. Each task
  gets an expected-runtime prediction, and shorter-predicted tasks get
  scheduled ahead of longer ones. These are two separate signals tracked
  independently, not the same number used two ways.

There is no fixed quantum size, per-level or otherwise. A **Planner**
decides how each task is broken into steps and produces that task's
expected-runtime estimate; there's no formula computing a constant.
This is specifically for heterogeneous traffic: a short web-search task
queued behind a long-running math task should be able to preempt it,
run to completion, and let the math task resume, rather than wait behind
it. That's the scenario preemption exists for, and SJF-with-preemption
(effectively SRTF, shortest-remaining-time-first) is what realizes it.

Every scheduling decision is observable: which queue a task sits in, why
it got preempted, why it got promoted or demoted, and how long it waited.

This only works if a worker's mid-task state can be paused and resumed
without losing progress, so "preemption" is cheap. That constraint drives
the whole design and is why it comes first below.

**Four layers, not two.** Earlier phases implied Scheduler → Workers.
There are actually two layers above the scheduler, with distinct jobs —
**naming these as two separate components (Orchestrator vs. Planner) is
my structural call, not something you named explicitly; flag it if you
want them merged or renamed:**

- **Orchestrator:** decides *how many* live workers exist right now,
  scaling that pool up or down elastically based on available resources
  (the AIMD concurrency policy below). Owns the worker pool. Nothing to
  do with task content.
- **Planner:** per task, decides how to break it into steps and predicts
  that task's expected runtime (the SJF ranking signal). Driven by an LLM
  call against a persistent **planning skill file** — an editable
  instructions/heuristics document, not a numeric formula — that the
  offline Sol job (Phase 7) rewrites based on where past predictions were
  wrong. This is the component that "learns" in this design; nothing else
  does.
- **Scheduler (MLFQ):** given whatever pool the orchestrator currently
  maintains and whatever estimates the planner currently attaches to each
  task, decides *which* ready task each free worker picks up next, and
  enforces the queue policy (SJF for levels 1-3, FIFO for level 4) and
  preemption.
- **Worker:** a *template*, not a fixed implementation. `generic_agent_harness`
  ships a `BaseWorker` (or similarly named abstract class) defining the
  agent-loop shape (reasoning turn → tool call → tool result →
  checkpoint-at-step-boundary → next turn) plus the hooks the scheduler
  and orchestrator need (`run_step()`, `checkpoint()`, `resume()`,
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
    2. Is the **offline learner** that calibrates the Planner (see
       "Offline job's role" below) by reviewing the day's LangSmith traces
       and rewriting the planning skill file the live Planner reads.
  - "Claude-like worker" describes the agent-loop *shape* (reasoning turn
    → tool call → tool result → next turn, same structure documented for
    the Claude/Codex loop in the lecture deck), not a specific model or
    backend — both the live and offline paths implement the same `Worker`
    interface.
- **Quantum:** counted in tool calls — this part is unchanged. There is
  **no fixed size, per-level or otherwise, and no formula computing one.**
  Each task's step boundaries are decided per-task by the Planner at
  runtime, not looked up from a table. This replaces the earlier
  "recompute between daily runs" framing entirely; that assumed a
  learned constant existed to recompute, and it doesn't.
- **Scheduling policy: 4-level MLFQ, SJF (levels 1-3) + FIFO (level 4).**
  Within levels 1-3, tasks are ordered by the Planner's expected-runtime
  estimate — shortest predicted first — and preemption makes this
  effectively SRTF: a newly-arrived short-predicted task can preempt a
  running longer one. Level 4 is a plain FIFO catch-all.
  **Still open, not yet decided by you:** the exact demotion rule for
  which tasks fall to level 4 (a task whose actual runtime blows past its
  own predicted estimate is the natural candidate, since that's the same
  signal the offline judge already needs — but that's my proposal, not a
  decision yet) and the promotion/aging rule preventing level-4
  starvation.
- **Offline job's role, corrected:** it does not update a numeric
  quantum/queue table. It's the **judge/calibrator of the Planner**:
  for each task, it compares the Planner's predicted runtime against the
  actually-observed runtime (from LangSmith traces — planner said 4s,
  task took 10s) and rewrites the **planning skill file** to correct
  systematic misestimation going forward. Also still runs the Phase 6
  LLM-as-judge output-quality scoring, a separate job from this
  calibration pass even though both run in the same daily Sol session.
- **Observability:** LangSmith tracing on every scheduling event and every
  worker LLM call (reusing the pattern already proven in Agent Harness),
  both to power the Phase 5 dashboards and as the raw data the offline
  Sol job reads to recalibrate the planning skill file and run
  LLM-as-judge scoring.
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
  live state on the same app rather than a second service. **The response
  contract for `POST /tasks` (task ID + separate status/result lookup vs.
  something else) is explicitly deferred by you** — noted here so it
  doesn't get silently decided by default when Phase 1 gets built.
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
  `api/` (the HTTP submission layer), `planner/`.
- Define the core abstractions as plain data classes before writing any
  scheduling logic: `Task`, `Worker` (as the subclassable template
  described above), `Quantum` (a work-based step boundary, size decided
  per-task by the Planner, not a constant), `Queue`, `SchedulerEvent`,
  `Planner`, `PlanningSkill` (the editable skill-file the offline job
  rewrites).
- No scheduling behavior yet. This phase just fixes vocabulary so Phase 1+
  isn't renaming things halfway through.

## Phase 1 — One worker, one task, checkpointable

- A minimal HTTP endpoint (`POST /tasks`) accepts the one task, since
  that's the standing decision for how tasks enter the system (response
  contract still deferred, see Decisions — a placeholder response is fine
  for this phase).
- The Planner runs once per task here too, even with nothing to schedule
  against yet: it decides the task's step boundaries and produces an
  expected-runtime estimate, using a seed/placeholder skill file (there's
  no calibration history yet, since that only exists after Phase 5+7 run
  at least once). The point of doing this now rather than bolting it on
  later is so the Planner's interface and the estimate-vs-actual data
  Phase 7 needs are already flowing before anything depends on them.
- Get a single Claude-like worker (a first concrete subclass of the
  `BaseWorker` template) to run that task end-to-end.
- Prove the worker's state can be serialized at a step boundary, the
  worker process stopped, and the task resumed later from that serialized
  state with no lost progress. This is the load-bearing primitive: if a
  task can't be paused and resumed cheaply, there is no MLFQ, just a
  worker pool with no real preemption.
- No queues, no priority, no concurrency, no orchestrator yet. Just:
  submit over HTTP, plan, run, pause at a step boundary, resume, finish.

## Phase 2 — Single queue, round robin, real preemption

- Multiple tasks, one worker, one FIFO queue. No SJF yet (a single FIFO
  queue has nothing to order by), no Planner-driven prioritization —
  purely proving preemption works at all.
- Scheduler hands the worker the head-of-queue task, lets it run for
  exactly one Planner-decided step, then actually preempts it (serialize
  state, re-enqueue at the tail) regardless of whether it finished.
- This is the first point where a step boundary is a real constraint
  instead of a concept on paper. Get this loop rock solid before adding
  priority.

## Phase 3 — Multi-level feedback queues (SJF + FIFO hybrid)

- 4 priority queues. Levels 1-3 order their tasks by the Planner's
  expected-runtime estimate (SJF); level 4 is plain FIFO.
- New tasks enter at level 1.
- Preemption is where this pays off for heterogeneous traffic: a
  short-predicted task arriving while a long-predicted one is running can
  preempt it (this is what makes the SJF ordering effectively SRTF, not
  just "sort once at arrival and never reconsider").
- **Demotion/promotion rule still needs deciding (proposed, not
  confirmed):** demote a task to the next level down when its actual
  runtime significantly exceeds its own Planner-predicted estimate — the
  same over/under-estimate signal Phase 7's offline judge uses, so
  demotion and calibration would both read from one source of truth
  instead of two. Aging/promotion at level 4 still needs its own rule so
  a task that keeps blowing its estimates doesn't starve forever.

## Phase 4 — Orchestrator: elastic worker pool

- A real worker pool, not a simulated one, but the pool size is no longer
  fixed. The Orchestrator layer scales the number of live worker
  coroutines to track pending-request backlog, up to a configured max
  concurrency, and drains back down as the queue empties (see Orchestrator
  scaling policy in Decisions). The scheduler still decides which ready
  task each currently-live worker picks up.
- **Max concurrency is adaptive, not pre-measured.** ASU RC's docs don't
  publish a rate limit for this gateway anywhere, which rules out finding
  one by deliberately ramping concurrent load until it breaks — that's a
  real, not hypothetical, risk of getting flagged for abusive traffic on
  a shared academic resource tied to your account. Instead, start
  conservative (e.g. concurrency = 2) and adjust live during normal
  operation, AIMD-style (the same shape as TCP congestion control, and
  close in spirit to `cold_email`'s pacing ramp, but reactive instead of
  a deliberate probe): increase by one after N consecutive clean
  successes, cut sharply (e.g. halve) on the first error or clear
  slowdown. The ceiling is discovered gradually through real traffic,
  never through a dedicated load test, and if ASU RC support can just
  tell you a real number (worth asking in their #rc-support Slack per
  their own docs) that becomes the starting ceiling instead.
- Also still open: what happens when the pool is already at max
  concurrency and a high-priority task arrives — does it preempt a
  running low-priority task early, or just wait for the next free/drained
  worker like everything else?

## Phase 5 — Observability

- Every scheduling decision emits a structured event: enqueue, dequeue,
  step start/end, preemption, demotion, promotion, starvation-aging
  trigger.
- Track per-task metrics: total wait time, number of preemptions, queue
  level over time, turnaround time, **and the Planner's predicted runtime
  alongside the actually-observed runtime** — Phase 7 can't calibrate
  anything without both numbers on the same task.
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
  should also decide what the live Planner does if a given day's offline
  run doesn't complete in time (keep using yesterday's planning skill
  file is the obvious default, but write it down rather than leaving it
  implicit).
- Reads the day's LangSmith traces (Phase 5) and, for each completed task,
  compares the Planner's predicted runtime to what actually happened.
  Rewrites the **planning skill file** to correct whatever systematic
  pattern shows up (e.g. "predictions for tasks involving X tool tend to
  run long, adjust upward"), for the live Planner to read starting next
  run. This is the only thing in the system that "learns" — it edits an
  instructions document, not a numeric table or a model's weights.
- Also runs the LLM-as-judge scoring pass used in Phase 6.
- Depends on Phase 5 existing (needs real predicted-vs-actual trace data
  to learn from) and on Phase 3's Planner/SJF machinery existing to
  produce predictions worth calibrating in the first place — so this
  phase's own code can start early, but it has nothing to plug into until
  then.
