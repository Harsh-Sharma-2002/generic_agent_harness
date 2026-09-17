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

There is no fixed quantum size, per-level or otherwise, and no per-task
LLM call decides the SJF ranking. This is specifically for heterogeneous
traffic: a short web-search task queued behind a long-running math task
should be able to preempt it, run to completion, and let the math task
resume, rather than wait behind it. That's the scenario preemption exists
for, and SJF-with-preemption (effectively SRTF, shortest-remaining-time-
first) is what realizes it.

**Two separate calibrated artifacts, not one.** Resolves a real tension:
SJF needs a runtime signal available *before* a task has a worker, but
task content should never leave the worker that's privately handling it
(same principle Agent Harness already uses — Web Search Agent and
Text2SQL Agent each keep private state, "one private pass"). So:

- **Admission table (shared, content-free):** a deterministic, hardcoded
  lookup, `task_class → time bucket`, consulted at submission for SJF
  ordering. No LLM call, no task content crosses into shared scheduler
  state, just a class label and a bucket number. `task_class` comes from
  how the synthetic benchmark tasks are authored (Task source decision
  below) — each one is tagged with a class at creation, so no
  inference step is needed at all. **Task class = tool profile as the
  primary key (e.g. "web-search-heavy," "long-computation,"
  "multi-tool-chain" — mirrors your own short-search-vs-long-math
  example directly), with expected step count as a secondary feature**,
  so the table has two axes to calibrate against, not one.
  **Cold start (day zero, before Phase 7 has ever run): every class maps
  to the same single bucket**, i.e. plain FIFO with no real SJF advantage
  yet. Deliberately honest about having zero information rather than
  guessing — and it means Phase 6 can measure "how many days of
  calibration until SJF actually beats FIFO," which is a result worth
  having on its own, not just a bootstrapping detail.
- **Planner (private, inside the Worker):** handles per-task step
  decomposition and in-flight re-estimation, with full access to task
  content, because it lives inside the worker actually running that task
  rather than a separate pre-scheduling component. Its output never
  needs to leave the worker except as plain numbers (e.g. "demote me,"
  "N steps remaining") for the scheduler to act on.

Every scheduling decision is observable: which queue a task sits in, why
it got preempted, why it got promoted or demoted, and how long it waited.

This only works if a worker's mid-task state can be paused and resumed
without losing progress, so "preemption" is cheap. That constraint drives
the whole design and is why it comes first below.

**Not an inference-serving optimization.** This operates at the task/job
level: deciding which queued task gets a worker next, to cut wait time
before a task starts running at all. That's a different layer from
things like vLLM's continuous batching or KV-cache management, which
optimize token generation throughput inside a single already-running
model-serving instance. Worth stating plainly since it's the kind of
distinction a paper reviewer will otherwise ask about.

**Three layers plus a private in-worker component.** Earlier phases
implied Scheduler → Workers; there's an Orchestrator above the scheduler,
and the Planner lives inside the Worker rather than as a fourth top-level
layer — **this placement is per your last message, superseding the
earlier "Planner as a separate layer" framing:**

- **Orchestrator:** decides *how many* live workers exist right now,
  scaling that pool up or down elastically based on available resources
  (the AIMD concurrency policy below). Owns the worker pool. Never sees
  task content.
- **Scheduler (MLFQ):** given whatever pool the orchestrator currently
  maintains, decides *which* ready task each free worker picks up next,
  using the admission table for initial SJF ordering and each running
  task's own numeric self-reports for anything after that. Enforces the
  queue policy (SJF for levels 1-3, FIFO for level 4) and preemption.
  Never sees task content either — only class labels, bucket numbers, and
  whatever plain numbers a worker's private Planner chooses to surface.
- **Worker:** a *template*, not a fixed implementation. `generic_agent_harness`
  ships a `BaseWorker` (or similarly named abstract class) defining the
  agent-loop shape (reasoning turn → tool call → tool result →
  checkpoint-at-step-boundary → next turn) plus the hooks the scheduler
  and orchestrator need (`run_step()`, `checkpoint()`, `resume()`,
  `is_done()`). A concrete task type subclasses it. This is what makes the
  repo a *generic* harness rather than a harness for one specific agent.
  **Owns its own Planner internally** — full access to task content,
  decides step decomposition and in-flight re-estimation privately, and
  is the only thing in the system driven by a per-task LLM call against
  the editable **planning skill file** (Phase 7 rewrites this based on
  where past in-flight predictions were wrong — separate from the
  admission table's calibration, see Decisions).

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
    2. Recalibrates **both** learning artifacts (see Quantum/Scheduling
       policy and Offline job's role below): the admission table and each
       worker's private planning skill file.
  - "Claude-like worker" describes the agent-loop *shape* (reasoning turn
    → tool call → tool result → next turn, same structure documented for
    the Claude/Codex loop in the lecture deck), not a specific model or
    backend. **Corrected:** the offline path is a one-shot judge/
    calibrator over trace data, not an agentic worker running
    checkpointable steps, so it does not implement the `Worker` interface
    the way the live path's task-executing workers do.
- **Quantum:** counted in tool calls — unchanged. Still no fixed size;
  step boundaries are decided per-task, privately, by the Worker's
  internal Planner, not looked up from any table.
- **Planner cadence: exception-driven, not fixed.** One LLM call plans
  the whole task up front (step decomposition + initial estimate). It is
  **not** re-invoked on a fixed schedule; it's re-invoked only when
  something already looks wrong, per your two triggers:
  1. A step's validation fails — retry that same step once against the
     existing plan first (most validation failures are transient, not a
     sign the plan itself is wrong); only trigger a full re-plan if it
     fails a second time in a row.
  2. A step's actual duration significantly overruns its allocated share
     of the original estimate — trigger a re-plan immediately, no retry,
     since this is a direct signal the original estimate was wrong rather
     than a transient failure. **The overrun threshold is per-`task_class`,
     not a fixed global ratio** (e.g. 1.5-2x): a short task naturally has
     much noisier relative timing than a long one, so one constant can't
     be right for both. This threshold is calibrated by the same Phase 7
     statistical pass that calibrates the admission table, not a
     hand-picked number.
  **Corrected after review — a real gap in the first version of this
  rule:** there was no cap on repeated failures. A task whose steps keep
  failing (bad tool, genuinely unsolvable step) could loop
  fail→retry→fail→re-plan indefinitely, hogging a worker forever — the
  same bug class already found for real in Agent Harness's `decisions.py`
  (the discovery loop caps itself at `MAX_DISCOVERY_ITERATIONS = 4`, the
  SQL retry loop doesn't cap at all). **`MAX_REPLAN_ATTEMPTS`** bounds
  this: once exceeded, the task fails outright instead of continuing to
  loop.
  This keeps the common case (task goes according to plan) to a single
  LLM call, and only pays for more when the plan has actually been
  falsified by something observed. **Still open:** whether to distinguish
  transient failures (timeout, rate limit — retry is worth it) from
  deterministic ones (malformed query, wrong argument shape — retrying
  the identical step will fail identically, so the retry is pure waste).
  Real refinement, adds complexity (requires classifying *why* a step
  failed), not yet decided whether it's worth it.
- **Reporting to the scheduler:** after every step (whether or not that
  step triggered a re-plan), the worker reports its current best
  remaining-time estimate as a plain number. If no re-plan happened, this
  is just the original estimate minus progress so far; if a re-plan
  happened, it's the revised number. The scheduler treats every such
  report as a fresh opportunity to reconsider preemption, so it doesn't
  need any separate polling mechanism.
- **Scheduling policy: 4-level MLFQ, SJF (levels 1-3) + FIFO (level 4).**
  Two distinct signals feed this, not one:
  - **At admission**, the deterministic `task_class → time bucket` table
    (no LLM, no content exposure) places a new task into level 1 and
    gives it its initial SJF ordering value.
  - **Once a task has a worker**, its private in-worker Planner can
    revise that number as real progress is observed, and the scheduler
    acts on the plain-number update — this is what lets preemption
    trigger even after admission, not just at arrival.
  Preemption makes this effectively SRTF: a task whose current
  (admission-table or worker-revised) estimate is shorter than what's
  running can preempt it. Level 4 is a plain FIFO catch-all.
  **Still open, not yet decided by you:** the exact demotion rule for
  which tasks fall to level 4 (a task whose actual runtime blows past its
  current estimate is the natural candidate, since that's the same signal
  the offline job already calibrates against — but that's my proposal,
  not a decision yet) and the promotion/aging rule preventing level-4
  starvation.
- **Offline job's role, corrected again:** two separate calibration
  passes, not one:
  1. **Admission table recalibration** — statistical, not LLM-based:
     group the day's completed tasks by `task_class`, compare actual
     runtime against the bucket they were assigned, and update the
     table's per-class bucket (e.g. shift a class's bucket up if it's
     consistently running longer than assigned). Easy to version and
     validate against a held-out slice before swapping in, since it's a
     small table, not free text.
  2. **Planning skill file recalibration** — for each task, compares the
     *worker's own* in-flight Planner predictions against what actually
     happened, and rewrites the skill file every worker's Planner reads,
     to correct systematic in-flight misestimation.
  Also still runs the Phase 6 LLM-as-judge output-quality scoring, a
  third, separate pass, even though all three run in the same daily Sol
  session.
- **Observability:** LangSmith tracing on every scheduling event and every
  worker LLM call (reusing the pattern already proven in Agent Harness),
  both to power the Phase 5 dashboards and as the raw data the offline
  Sol job reads for both calibration passes and LLM-as-judge scoring.
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

- Repo layout: `worker/` (Planner lives inside this package, not its own
  top-level one), `scheduler/`, `observability/`, `tasks/`, `api/` (the
  HTTP submission layer).
- Define the core abstractions as plain data classes before writing any
  scheduling logic: `Task` (carries a `task_class` label — tool profile
  as primary key, expected step count as a secondary feature), `Worker` (as
  the subclassable template described above, owns a `Planner` instance
  internally), `Quantum` (a work-based step boundary, size decided
  privately per-task by the Worker's Planner, not a constant), `Queue`,
  `SchedulerEvent`, `PlanningSkill` (the worker-private skill-file the
  offline job rewrites), `AdmissionTable` (the shared `task_class → time
  bucket` lookup, also offline-job-rewritten but statistically, not via
  LLM). `MAX_REPLAN_ATTEMPTS` is a constant here too, from day one — not
  something to bolt on after hitting the retry-loop bug for real.
- No scheduling behavior yet. This phase just fixes vocabulary so Phase 1+
  isn't renaming things halfway through.

## Phase 1 — One worker, one task, checkpointable

- A minimal HTTP endpoint (`POST /tasks`) accepts the one task, since
  that's the standing decision for how tasks enter the system (response
  contract still deferred, see Decisions — a placeholder response is fine
  for this phase). The task carries a `task_class` label, authored in
  directly since tasks are synthetic (Task source decision).
- Get a single Claude-like worker (a first concrete subclass of the
  `BaseWorker` template, owning its own Planner internally) to run that
  task end-to-end. The Planner decides step boundaries and produces
  in-flight estimates privately, using a seed/placeholder skill file
  (there's no calibration history yet, since that only exists after
  Phase 5+7 run at least once). Doing this now rather than bolting it on
  later means the Planner's interface and the estimate-vs-actual data
  Phase 7 needs are already flowing before anything depends on them. No
  admission table needed yet — that only matters once there's more than
  one task to rank (Phase 3).
- Prove the worker's state can be serialized at a step boundary, the
  worker process stopped, and the task resumed later from that serialized
  state with no lost progress. This is the load-bearing primitive: if a
  task can't be paused and resumed cheaply, there is no MLFQ, just a
  worker pool with no real preemption.
- No queues, no priority, no concurrency, no orchestrator yet. Just:
  submit over HTTP, plan, run, pause at a step boundary, resume, finish.

## Phase 2 — Single queue, round robin, real preemption

- Multiple tasks, one worker, one FIFO queue. No SJF yet (a single FIFO
  queue has nothing to order by), no admission table needed — purely
  proving preemption works at all.
- Scheduler hands the worker the head-of-queue task, lets it run for
  exactly one step (boundary decided privately by that worker's own
  Planner), then actually preempts it (serialize state, re-enqueue at the
  tail) regardless of whether it finished.
- This is the first point where a step boundary is a real constraint
  instead of a concept on paper. Get this loop rock solid before adding
  priority.

## Phase 3 — Multi-level feedback queues (SJF + FIFO hybrid)

- 4 priority queues. Levels 1-3 order tasks by SJF; level 4 is plain
  FIFO. The SJF value starts as an admission-table lookup
  (`task_class → time bucket`, no LLM call) and gets revised by that
  task's own worker-private Planner once it's running, so ordering can
  change mid-flight, not just at arrival.
- New tasks enter at level 1.
- Preemption is where this pays off for heterogeneous traffic: a
  short-predicted task arriving while a long-predicted one is running can
  preempt it (this is what makes the SJF ordering effectively SRTF, not
  just "sort once at arrival and never reconsider").
- **Demotion/promotion rule still needs deciding (proposed, not
  confirmed):** demote a task to the next level down when its actual
  runtime significantly exceeds its current estimate — the same
  over/under-estimate signal Phase 7's offline calibration passes use, so
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
- **The concurrency cap is a local-run constraint, not a ceiling on the
  design.** This phase is built and tested locally, bounded by the free
  ASU gateway and local machine resources, so a low, adaptive cap is
  correct here. On a real cloud deployment the same backlog-driven
  scaling mechanism applies with a much higher (or effectively
  unbounded) ceiling — the elasticity claim is about the mechanism
  (scale count tracks backlog), not about how high this particular local
  setup happens to scale.
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
  trigger, retry, re-plan, and `MAX_REPLAN_ATTEMPTS` failure — a task
  that fails outright needs to be as visible in the traces as one that
  succeeds, or Phase 6's results silently exclude exactly the tasks most
  likely to reveal scheduling problems.
- Track per-task metrics: total wait time, number of preemptions, queue
  level over time, turnaround time, **the task's `task_class` and its
  admission-table bucket, and the worker's private Planner's predicted
  runtime, alongside the actually-observed runtime** — Phase 7 can't run
  either calibration pass without all of these on the same task.
- Export these as LangSmith traces, both for the live scheduler dashboard
  and as the dataset the Phase 7 offline job reads.

## Phase 6 — Evaluation (the research-paper payoff)

- **Baseline, now precisely defined:** the identical system (same Worker,
  same private Planner, same step mechanism) with the scheduler ignoring
  SJF ordering and using plain FIFO/round-robin instead. This isolates
  the actual variable under test (the scheduling policy) instead of
  comparing against a differently-built system.
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
  should also decide what the live system does if a given day's offline
  run doesn't complete in time (keep using yesterday's admission table
  and skill file is the obvious default, but write it down rather than
  leaving it implicit).
- Reads the day's LangSmith traces (Phase 5) and runs **two separate
  calibration passes**:
  1. **Admission table** — statistical: group completed tasks by
     `task_class`, compare actual runtime to the bucket each was
     assigned, adjust that class's bucket. Small table, easy to version
     and validate against a held-out slice before it replaces the live
     one.
  2. **Planning skill file** — for each task, compares that task's own
     worker-private Planner prediction to what actually happened, and
     rewrites the skill file every worker's Planner reads, to correct
     systematic in-flight misestimation. This one edits an instructions
     document, not a table, so it has no equivalent natural regularization
     — worth applying the same held-out-validation-before-swap discipline
     here too, even though the mechanism is fuzzier.
- Also runs the LLM-as-judge scoring pass used in Phase 6.
- Depends on Phase 5 existing (needs real predicted-vs-actual trace data
  for both passes) and on Phase 3's admission-table/SJF machinery and
  Phase 1's Planner existing to produce predictions worth calibrating in
  the first place — so this phase's own code can start early, but it has
  nothing to plug into until then.
