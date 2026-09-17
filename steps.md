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

There is no fixed quantum size, per-level or otherwise, and no dedicated
"planner" component decides the SJF ranking. This is specifically for
heterogeneous traffic: a short web-search task queued behind a
long-running math task should be able to preempt it, run to completion,
and let the math task resume, rather than wait behind it. That's the
scenario preemption exists for, and SJF-with-preemption (effectively
SRTF, shortest-remaining-time-first) is what realizes it.

**Two generic node types, not a growing list of named components.** The
whole worker is built from exactly two primitives, each specialized by
which markdown file it reads rather than by being a bespoke class:

- **`LLMCaller`:** makes one LLM call. What it actually does — decompose
  a task and produce an estimate, continue an existing plan, judge output
  quality, whatever — comes from how the Worker's own code prompts it at
  that point, on top of a single governing markdown file (`llm_caller.md`)
  that defines its general behavior/conventions. **My reading of "each
  node reads their own md," not something you specified in this much
  detail — flag it if you meant one markdown file per *role* (planning,
  execution, judging) instead of one per *node type* with role-specific
  prompting layered on top.**
- **`ToolCaller`:** executes an actual tool call, reading its own
  `tool_caller.md` the same way.

A **step/quantum is exactly one `LLMCaller` invocation plus the
`ToolCaller` call(s) it triggers** — the same reasoning-turn → tool-call
→ tool-result loop already documented for the Claude/Codex agent loop in
the lecture deck, now also the concrete definition of the preemption
boundary. This replaces the earlier design's separate "Planner"
component; decomposition and runtime estimation are just one particular
thing an `LLMCaller` is asked to do (typically on a task's first
invocation), not a different component with different rules.

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
  below) — each one is tagged with a class at creation, so no inference
  step is needed at all. **Task class = tool profile as the primary key**
  (e.g. "web-search-heavy," "long-computation," "multi-tool-chain" —
  mirrors your own short-search-vs-long-math example directly), **with
  expected step count as a secondary feature**, so the table has two axes
  to calibrate against, not one. **Cold start (day zero, before the
  offline job has ever run): every class maps to the same single
  bucket**, i.e. plain FIFO with no real SJF advantage yet. Deliberately
  honest about having zero information rather than guessing — and it
  means the evaluation phase can measure "how many days of calibration
  until SJF actually beats FIFO," a result worth having on its own, not
  just a bootstrapping detail. This stays exactly as designed regardless
  of the `LLMCaller`/`ToolCaller` simplification — it was never an
  LLM-driven artifact, and shouldn't become one, since that would
  reintroduce the cost/latency/privacy problem it was built to avoid.
- **`LLMCaller`'s in-flight behavior (private, inside the Worker):**
  handles per-task step decomposition and in-flight re-estimation, with
  full access to task content, because it lives inside the worker
  actually running that task rather than a separate pre-scheduling
  component. Its output never needs to leave the worker except as plain
  numbers (e.g. "demote me," "N steps remaining") for the scheduler to
  act on.

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

**Three layers.** Earlier phases implied Scheduler → Workers; there's an
Orchestrator above the scheduler too:

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
  whatever plain numbers a worker's `LLMCaller` chooses to surface.
- **Worker:** a *template*, not a fixed implementation. `generic_agent_harness`
  ships a `BaseWorker` (or similarly named abstract class) built from an
  `LLMCaller` and a `ToolCaller`, plus the hooks the scheduler and
  orchestrator need (`run_step()`, `checkpoint()`, `resume()`,
  `is_done()`). A concrete task type subclasses it. Keeping the worker
  down to two generic, markdown-configured primitives (rather than
  bespoke components like a dedicated "Planner" class) is what makes the
  repo a genuinely *generic* template, not just a harness for one
  specific agent with extra reusable-sounding names.

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
       tasks (Phase 7), scoring output quality with a stronger model than
       whatever's answering live requests, so the judge isn't grading its
       own homework.
    2. Recalibrates **both** learning artifacts (see Scheduling policy
       and Offline job's role below): the admission table and the
       `LLMCaller`/`ToolCaller` markdown files.
  - "Claude-like worker" describes the agent-loop *shape* (reasoning turn
    → tool call → tool result → next turn, same structure documented for
    the Claude/Codex loop in the lecture deck), not a specific model or
    backend. The offline path is a one-shot judge/calibrator over trace
    data, not an agentic worker running checkpointable steps, so it does
    not implement the `Worker` interface the way the live path's
    task-executing workers do.
- **Quantum:** counted in tool calls — unchanged. Still no fixed size;
  a step boundary is one `LLMCaller` invocation plus the `ToolCaller`
  call(s) it triggers, decided in the moment, not looked up from any
  table or decided by a separate component.
- **`LLMCaller` invocation cadence: exception-driven, not fixed.** One
  call plans the whole task up front (step decomposition + initial
  estimate). It is **not** re-invoked on a fixed schedule; it's
  re-invoked only when something already looks wrong, per your two
  triggers:
  1. A step's validation fails — retry that same step once against the
     existing plan first (most validation failures are transient, not a
     sign the plan itself is wrong); only trigger a fresh `LLMCaller`
     invocation to re-plan if it fails a second time in a row.
  2. A step's actual duration significantly overruns its allocated share
     of the original estimate — trigger a re-plan invocation immediately,
     no retry, since this is a direct signal the original estimate was
     wrong rather than a transient failure. **The overrun threshold is
     per-`task_class`, not a fixed global ratio** (e.g. 1.5-2x): a short
     task naturally has much noisier relative timing than a long one, so
     one constant can't be right for both. This threshold is calibrated
     by the same statistical pass that calibrates the admission table,
     not a hand-picked number.
  **`MAX_REPLAN_ATTEMPTS` bounds repeated failures**, so a task whose
  steps keep failing (bad tool, genuinely unsolvable step) can't loop
  fail→retry→fail→re-plan indefinitely and hog a worker forever — the
  same bug class already found for real in Agent Harness's `decisions.py`
  (the discovery loop caps itself at `MAX_DISCOVERY_ITERATIONS = 4`, the
  SQL retry loop doesn't cap at all). Once exceeded, the task fails
  outright instead of continuing to loop. This keeps the common case
  (task goes according to plan) to a single LLM call, and only pays for
  more when the plan has actually been falsified by something observed.
  **Still open:** whether to distinguish transient failures (timeout,
  rate limit — retry is worth it) from deterministic ones (malformed
  query, wrong argument shape — retrying the identical step will fail
  identically, so the retry is pure waste). Real refinement, adds
  complexity (requires classifying *why* a step failed), not yet decided
  whether it's worth it.
- **Reporting to the scheduler:** after every step (whether or not that
  step triggered a re-plan invocation), the worker reports its current
  best remaining-time estimate as a plain number. If no re-plan happened,
  this is just the original estimate minus progress so far; if a re-plan
  happened, it's the revised number. The scheduler treats every such
  report as a fresh opportunity to reconsider preemption, so it doesn't
  need any separate polling mechanism.
- **Scheduling policy: 4-level MLFQ, SJF (levels 1-3) + FIFO (level 4).**
  Two distinct signals feed this, not one:
  - **At admission**, the deterministic `task_class → time bucket` table
    (no LLM, no content exposure) places a new task into level 1 and
    gives it its initial SJF ordering value.
  - **Once a task has a worker**, its `LLMCaller` can revise that number
    as real progress is observed, and the scheduler acts on the
    plain-number update — this is what lets preemption trigger even after
    admission, not just at arrival.
  Preemption makes this effectively SRTF: a task whose current
  (admission-table or worker-revised) estimate is shorter than what's
  running can preempt it. Level 4 is a plain FIFO catch-all.
  **Still open, not yet decided by you:** the exact demotion rule for
  which tasks fall to level 4 (a task whose actual runtime blows past its
  current estimate is the natural candidate, since that's the same signal
  the offline job already calibrates against — but that's my proposal,
  not a decision yet) and the promotion/aging rule preventing level-4
  starvation.
- **Offline job's role:** two separate calibration passes, not one:
  1. **Admission table recalibration** — statistical, not LLM-based:
     group the day's completed tasks by `task_class`, compare actual
     runtime against the bucket they were assigned, and update the
     table's per-class bucket (e.g. shift a class's bucket up if it's
     consistently running longer than assigned). Easy to version and
     validate against a held-out slice before swapping in, since it's a
     small table, not free text.
  2. **`llm_caller.md` (and, if warranted, `tool_caller.md`)
     recalibration** — for each task, compares the worker's own in-flight
     `LLMCaller` predictions against what actually happened, and rewrites
     the markdown file every worker's `LLMCaller` reads, to correct
     systematic in-flight misestimation. Edits an instructions document,
     not a table, so it has no equivalent natural regularization — apply
     the same held-out-validation-before-swap discipline here too, even
     though the mechanism is fuzzier.
  Also still runs the LLM-as-judge output-quality scoring, a third,
  separate pass, even though all three run in the same daily Sol session.
- **Observability:** LangSmith tracing on every scheduling event and every
  worker LLM call (reusing the pattern already proven in Agent Harness),
  both to power the observability phase's dashboards and as the raw data
  the offline job reads for both calibration passes and LLM-as-judge
  scoring.
- **Stack:** Python, asyncio.
- **Task source:** synthetic benchmark tasks that we author, so the
  evaluation phase is controlled and repeatable.
- **Task submission:** HTTP API in Phase 1 (placeholder), superseded by
  A2A in Phase 2 — see Phase 2 for why. Chosen deliberately over an
  in-process call because the goal is to keep this close to how it would
  run in production.
- **Security & observability layer** (FastAPI middleware on the existing
  `api/` layer, not a new named architectural layer — applies from
  Phase 1 onward, to whichever protocol is fronting the system at the
  time):
  - **Auth: per-user API keys, multi-tenant.** Each user has their own
    key; keys are **hashed, never stored raw** (this repo is public — a
    DB leak of hashes is not a leak of usable keys). Storage: **SQLite**,
    matching the pattern already used in `cold_email`/`job_search`, the
    simplest fit for a single-machine research prototype.
  - **Per-user rate limiting:** caps requests/time from a single key,
    independent of the Orchestrator's own backlog-driven scaling. Note
    this only addresses one user flooding the system — the "no
    backpressure under aggregate load from many well-behaved users" gap
    (flagged earlier) is separate and still open.
  - **Audit logging:** every request logged with who (key/user id), what
    (`task_class` only, never task content — same privacy principle as
    the rest of the design), when, and outcome
    (admitted/rejected/rate-limited). Feeds the same LangSmith pipeline
    already built for scheduling events, not a second system.
  - **Input validation:** malformed/oversized task payloads rejected
    before reaching admission logic.
  - **`user.md`:** a per-user markdown file that personalizes the
    `LLMCaller`'s behavior for that user's tasks, loaded alongside
    `llm_caller.md`. **Starts as user-authored/static, not
    offline-calibrated** — calibrating one file per user would grow the
    offline job's scope meaningfully and only matters once there's more
    than one real user. Noted as explicit future scope, not built now.
  - **Explicitly deferred:** fairness across users *within* the worker
    pool once tasks are admitted (rate limiting only governs submission
    rate, not whether one user's admitted tasks crowd out another's SJF
    slots) — matters only with multiple real concurrent users, which
    isn't the near-term reality.
- **Orchestrator scaling policy:** driven by backlog, not by error/latency
  signals. Reasoning given: once the system is already resource-saturated,
  launching more workers doesn't help, so error rate isn't a useful
  scale-*up* signal, only a scale-*down*/ceiling one, and the ceiling is
  simpler to just set directly. Concretely: active worker coroutines track
  the number of pending requests, up to a **configured max concurrency**
  (a number reflecting known/assumed gateway capacity, not yet picked —
  see open item in Phase 5), and scale back down as workers finish and
  the queue empties.
- **Scaled instance = asyncio coroutine** within one process (not a
  separate OS process or node).
- **Scale-down behavior = graceful drain**: a worker being scaled down
  finishes its current quantum, then isn't given another task; no
  mid-quantum hard preemption for this specific case.

## Phase 0 — Scaffold & design doc

- Repo layout: `worker/` (contains `LLMCaller`, `ToolCaller`, and a
  `skills/` subfolder holding `llm_caller.md` and `tool_caller.md`),
  `scheduler/`, `observability/`, `tasks/`, `api/` (the HTTP/A2A
  submission layer), `security/` (auth middleware, the `User`/`ApiKey`
  SQLite store, rate limiting, audit logging).
- Define the core abstractions as plain data classes before writing any
  scheduling logic: `Task` (carries a `task_class` label — tool profile
  as primary key, expected step count as a secondary feature), `Worker`
  (as the subclassable template described above, built from an
  `LLMCaller` and a `ToolCaller`), `Quantum` (a work-based step boundary
  — one `LLMCaller` invocation plus its `ToolCaller` call(s), not a
  constant), `Queue`, `SchedulerEvent`, `NodeSkill` (a small versioned
  wrapper around a markdown file — `llm_caller.md`, `tool_caller.md`,
  and `user.md` all use this, so the held-out-validation-before-swap
  discipline applies uniformly), `AdmissionTable` (the shared
  `task_class → time bucket` lookup, offline-job-rewritten statistically,
  never via LLM), `User`/`ApiKey` (hashed-key storage). `MAX_REPLAN_ATTEMPTS`
  is a constant here too, from day one — not something to bolt on after
  hitting the retry-loop bug for real.
- No scheduling behavior yet. This phase just fixes vocabulary so Phase 1+
  isn't renaming things halfway through.

## Phase 1 — One worker, one task, checkpointable

- A minimal HTTP endpoint (`POST /tasks`) accepts the one task — a
  deliberate placeholder, since Phase 2 replaces it with A2A. The
  security middleware (API key auth, rate limiting, audit logging, input
  validation) wraps this endpoint from day one rather than being
  retrofitted later. The task carries a `task_class` label, authored in
  directly since tasks are synthetic (Task source decision).
- Get a single Claude-like worker (a first concrete subclass of the
  `BaseWorker` template, built from an `LLMCaller` and a `ToolCaller`) to
  run that task end-to-end. The `LLMCaller`'s first invocation for a task
  decides step boundaries and produces an in-flight estimate, using
  seed/placeholder `llm_caller.md` and `tool_caller.md` files (there's no
  calibration history yet, since that only exists after the observability
  and offline-loop phases run at least once). Doing this now rather than
  bolting it on later means the estimate-vs-actual data those later
  phases need is already flowing before anything depends on it. No
  admission table needed yet — that only matters once there's more than
  one task to rank (Phase 4).
- Prove the worker's state can be serialized at a step boundary, the
  worker process stopped, and the task resumed later from that serialized
  state with no lost progress. This is the load-bearing primitive: if a
  task can't be paused and resumed cheaply, there is no MLFQ, just a
  worker pool with no real preemption.
- No queues, no priority, no concurrency, no orchestrator yet. Just:
  submit over HTTP, plan, run, pause at a step boundary, resume, finish.

## Phase 2 — A2A protocol integration

- Replaces Phase 1's placeholder `POST /tasks` with a proper
  [A2A-protocol](https://a2a-protocol.org/latest/specification/)-compliant
  interface (v1.0, Linux Foundation): an Agent Card served at
  `/.well-known/agent.json` (declaring supported `task_class`es and an
  auth scheme), JSON-RPC 2.0 over HTTPS as the transport, and tasks
  following A2A's own lifecycle (`submitted → working → input-required →
  completed/canceled/failed`) mapped onto this project's task states.
- **This is what actually resolves the response-contract question left
  open in Decisions**, rather than needing a bespoke task-ID-plus-polling
  design: A2A already defines how a client gets a result back for a
  long-running, unpredictable-duration task — synchronously, over
  Server-Sent Events, or via a callback URL.
- **Auth, unified rather than duplicated:** A2A's Agent Card declares an
  auth scheme; this uses the same per-user API-key system from Decisions
  as that scheme, instead of building a second auth mechanism just for
  A2A.
- Still runs against the exact single-worker-single-task system proven in
  Phase 1 — no queues, priority, or orchestrator yet. Validates the
  protocol implementation against the simplest possible backend before
  scheduling complexity gets layered on top starting Phase 3, so a bug
  is either "the protocol layer" or "the core primitive," never both at
  once.
- Explicitly sequenced here — after the core checkpoint/resume primitive
  is proven, before MLFQ/orchestrator complexity exists — rather than
  either before Phase 1 (nothing to validate it against yet) or after the
  full scheduler (debugging protocol compliance and scheduling logic
  simultaneously). This phase exists primarily as a learning goal
  alongside the paper's critical path, not because the paper's scheduling
  claims depend on it — worth keeping in mind if time gets tight later.

## Phase 3 — Single queue, round robin, real preemption

- Multiple tasks, one worker, one FIFO queue. No SJF yet (a single FIFO
  queue has nothing to order by), no admission table needed — purely
  proving preemption works at all.
- Scheduler hands the worker the head-of-queue task, lets it run for
  exactly one step (one `LLMCaller` invocation plus its `ToolCaller`
  call(s)), then actually preempts it (serialize state, re-enqueue at the
  tail) regardless of whether it finished.
- This is the first point where a step boundary is a real constraint
  instead of a concept on paper. Get this loop rock solid before adding
  priority.

## Phase 4 — Multi-level feedback queues (SJF + FIFO hybrid)

- 4 priority queues. Levels 1-3 order tasks by SJF; level 4 is plain
  FIFO. The SJF value starts as an admission-table lookup
  (`task_class → time bucket`, no LLM call) and gets revised by that
  task's own worker's `LLMCaller` once it's running, so ordering can
  change mid-flight, not just at arrival.
- New tasks enter at level 1.
- Preemption is where this pays off for heterogeneous traffic: a
  short-predicted task arriving while a long-predicted one is running can
  preempt it (this is what makes the SJF ordering effectively SRTF, not
  just "sort once at arrival and never reconsider").
- **Demotion/promotion rule still needs deciding (proposed, not
  confirmed):** demote a task to the next level down when its actual
  runtime significantly exceeds its current estimate — the same
  over/under-estimate signal the offline job's calibration passes use, so
  demotion and calibration would both read from one source of truth
  instead of two. Aging/promotion at level 4 still needs its own rule so
  a task that keeps blowing its estimates doesn't starve forever.

## Phase 5 — Orchestrator: elastic worker pool

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

## Phase 6 — Observability

- Every scheduling decision emits a structured event: enqueue, dequeue,
  step start/end, preemption, demotion, promotion, starvation-aging
  trigger, retry, re-plan, and `MAX_REPLAN_ATTEMPTS` failure — a task
  that fails outright needs to be as visible in the traces as one that
  succeeds, or the evaluation phase's results silently exclude exactly
  the tasks most likely to reveal scheduling problems.
- Track per-task metrics: total wait time, number of preemptions, queue
  level over time, turnaround time, **the task's `task_class` and its
  admission-table bucket, and the worker's `LLMCaller` predicted runtime,
  alongside the actually-observed runtime** — the offline job can't run
  either calibration pass without all of these on the same task.
- Export these as LangSmith traces, both for the live scheduler dashboard
  and as the dataset the offline job (Phase 8) reads.

## Phase 7 — Evaluation (the research-paper payoff)

- **Baseline, precisely defined:** the identical system (same Worker,
  same `LLMCaller`/`ToolCaller`, same step mechanism) with the scheduler
  ignoring SJF ordering and using plain FIFO/round-robin instead. This
  isolates the actual variable under test (the scheduling policy) instead
  of comparing against a differently-built system.
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

## Phase 8 — Offline daily learning loop (Sol)

- A Slurm batch job on Sol, run once a day, hosting Qwen2.5-72B-Instruct
  via vLLM (`4×80G A100s`, per
  [ASU's docs](https://docs.rc.asu.edu/vllm)) for the duration of that
  job only. Reached via SSH tunnel, not a persistent endpoint. A 4×80G
  request on a shared cluster may queue behind other jobs, so this phase
  should also decide what the live system does if a given day's offline
  run doesn't complete in time (keep using yesterday's admission table
  and markdown files is the obvious default, but write it down rather
  than leaving it implicit).
- Reads the day's LangSmith traces (Phase 6) and runs **two separate
  calibration passes**:
  1. **Admission table** — statistical: group completed tasks by
     `task_class`, compare actual runtime to the bucket each was
     assigned, adjust that class's bucket. Small table, easy to version
     and validate against a held-out slice before it replaces the live
     one.
  2. **`llm_caller.md` / `tool_caller.md`** — for each task, compares the
     worker's own in-flight `LLMCaller` prediction to what actually
     happened, and rewrites whichever markdown file every worker reads,
     to correct systematic in-flight misestimation. (`user.md` is
     explicitly excluded from this pass for now — see Decisions.)
- Also runs the LLM-as-judge scoring pass used in Phase 7.
- Depends on Phase 6 existing (needs real predicted-vs-actual trace data
  for both passes) and on Phase 4's admission-table/SJF machinery and
  Phase 1's `LLMCaller` existing to produce predictions worth calibrating
  in the first place — so this phase's own code can start early, but it
  has nothing to plug into until then.
