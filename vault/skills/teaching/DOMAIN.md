# Teaching

Student-facing learning methodology: how a learning interaction is run so that the learner does the cognitive work — retrieving, explaining, being helped gradually, diagnosing errors, calibrating confidence, applying knowledge elsewhere, and performing unaided.

## When to Use

Use this domain when the Task's objective is someone's **learning or understanding**, not the production of content:

- reviewing material the learner has previously encountered
- working through a problem the learner is stuck on, or has got wrong
- helping the learner explain, justify, or apply a concept themselves
- building or reviewing study strategy, study habits, and self-regulation
- establishing what the learner can now do without assistance
- evaluating or questioning the trustworthiness of AI-produced explanations the learner is relying on

The presence of an educational topic is not sufficient. A Task whose subject matter happens to be academic should route here only when the objective involves learning, practice, understanding, error diagnosis, scaffolding, transfer, self-regulation, or independent performance.

## When Not to Use

- The Task is to **produce** teaching material — a summary, handout, lesson plan, syllabus, or explanation to be read or delivered. That is content production; route by the content's subject.
- The Task is to answer a question whose subject is educational, but nothing about a learner's understanding or practice changes.
- The Task asks for software or data work. Use `coding`, `data`, or `research` as appropriate.

## Skills

### teaching.ai-claim-checker

**Use when:** A substantive AI-generated explanation has been given and the learner should treat it as a claim to evaluate rather than truth to absorb.

Requires a checking plan from the learner: name a claim worth checking, propose how to verify it, and identify an independent source to consult.

**Avoid when:** No AI-generated claim is in play and the objective is the learner's own reasoning; use `teaching.explain-first-interrogator`.

### teaching.confidence-calibration-check

**Use when:** The point is the accuracy of the learner's self-assessment — overconfidence or underconfidence relative to demonstrated performance.

Captures a confidence rating before an attempt and again after feedback, then compares the two against what was actually produced.

**Avoid when:** The objective is content review rather than self-assessment accuracy; use `teaching.retrieve-first-gate`.

### teaching.explain-first-interrogator

**Use when:** The learner should articulate a concept or their reasoning in their own words before the AI evaluates, corrects, or extends it.

Gates AI contribution behind the learner's own explanation, then probes the weakest part of it while preserving learner ownership.

### teaching.fading-manager

**Use when:** Competence is being demonstrated across sessions and the amount of support should be reduced accordingly.

Systematically withdraws scaffolding as performance supports it, names each transition to the learner, and periodically removes all support to test independence.

**Avoid when:** The Task concerns a single session's plan, monitoring, and reflection; use `teaching.srl-session-wrapper`.

### teaching.productive-failure-protocol

**Use when:** A problem is novel and genuinely beyond current competence, and exploration before instruction is the point.

Stages contrasting attempted approaches inside an agreed finite window before consolidation, then builds the consolidation on the learner's own attempts.

**Avoid when:** The learner is already working a problem and needs help progressing; use `teaching.progressive-hint-ladder`.

### teaching.progressive-hint-ladder

**Use when:** A learner is stuck mid-problem and should be helped forward without being handed the answer.

Provides graduated assistance from an abstract nudge toward a near-complete scaffold, requiring reflection at each level before escalating, and never supplies the full solution.

**Avoid when:** The learner cannot say what they are stuck on; use `teaching.stuck-and-error-diagnosis-coach` first.

### teaching.retrieve-first-gate

**Use when:** The learner is reviewing previously encountered material and help should be structured around what they actually recall.

Invites free recall with a confidence rating before any explanation, then maps what was accurate, missing, and misconceived.

**Avoid when:** The material is new to the learner; foundation teaching is appropriate instead.

### teaching.srl-session-wrapper

**Use when:** A study session should be structured around planning, monitoring, and reflecting.

Wraps the session in a set goal, a mid-session check of whether the approach is working, and a closing reflection on what changed and what comes next.

**Avoid when:** The review spans many past sessions; use `teaching.weekly-agency-review`.

### teaching.stuck-and-error-diagnosis-coach

**Use when:** The learner got something wrong or is stuck and cannot locate where it breaks down, and targeted help requires that diagnosis first.

Requires the learner to name what they tried, where it breaks down, and what kind of error it looks like, then structures help around their diagnosis.

### teaching.teach-back-evaluator

**Use when:** The learner's explanation of a concept should be tested by having them teach it.

Learns from the learner in a curious-novice role, asks clarifying questions that expose gaps, and gives formative feedback on coherence, completeness, and misconception risk.

**Avoid when:** The question is whether the knowledge applies somewhere new; use `teaching.transfer-bridge`.

### teaching.transfer-bridge

**Use when:** The learner has demonstrated understanding and the question is whether it is portable beyond the context where it was learned.

Presents near-transfer and far-transfer challenges and asks what is the same, what differs, and which principle travels across the contexts.

**Avoid when:** The question is explanation quality rather than application; use `teaching.teach-back-evaluator`.

### teaching.unassisted-evidence-checkpoint

**Use when:** After scaffolded practice, the learner's performance with no help at all needs to be observed.

Schedules an unaided attempt with no hints or evaluative feedback and records it separately from assisted performance.

**Avoid when:** The session is still scaffolded and assistance is expected; use `teaching.progressive-hint-ladder`.

### teaching.weekly-agency-review

**Use when:** A week or multi-session period should be reviewed against accumulated evidence of how the learner studied.

Presents patterns from prior sessions — attempt rates, hint depths, calibration, transfer and unassisted results — asks the learner to interpret them first, and closes with a learner-set strategy goal.

## Routing Distinctions

**Recall versus explanation.** `teaching.retrieve-first-gate` works on what the learner already holds and is appropriate for previously encountered material. `teaching.explain-first-interrogator` works on reasoning in progress: the learner explains the concept at hand. Both gate AI contribution behind learner output; they differ in whether the target is memory of prior material or articulation of live thinking.

**Graduated help versus error diagnosis.** Use `teaching.progressive-hint-ladder` when the learner knows where they are stuck and needs help moving forward. Use `teaching.stuck-and-error-diagnosis-coach` when the breakdown is unclear or the learner has produced an error they cannot explain. When the learner cannot name what is wrong, diagnosis precedes hinting.

**Explanation, transfer, and independence.** `teaching.teach-back-evaluator` judges the quality of the learner's explanation. `teaching.transfer-bridge` tests whether that understanding works in a different context. `teaching.unassisted-evidence-checkpoint` measures performance with no support at all. They answer different questions — can they explain it, does it travel, can they do it alone — and should not be substituted for one another.

**Session, multi-session, and scaffolding level.** `teaching.srl-session-wrapper` structures one session's plan, monitoring, and reflection. `teaching.weekly-agency-review` looks across past sessions at patterns in how the learner studied. `teaching.fading-manager` is the only one of the three that changes the amount of support being provided.

**Exploration versus assistance.** `teaching.productive-failure-protocol` deliberately withholds instruction while the learner attempts approaches to a novel problem. `teaching.progressive-hint-ladder` supplies graduated assistance on a problem already in progress. Assigning both at once conflicts over whether help is being offered.

**Calibration versus retrieval.** The retrieve-first gate collects a confidence rating as part of recall. `teaching.confidence-calibration-check` is the standalone comparison of confidence against demonstrated performance. Assign calibration when self-assessment accuracy is the objective, not content review.

**Trusting AI output versus owning one's reasoning.** `teaching.ai-claim-checker` targets the trustworthiness of an AI-produced explanation the learner is relying on. `teaching.explain-first-interrogator` targets the learner's own reasoning. They can follow one another but are not interchangeable.

## Assignment Principle

Prefer the smallest sufficient set of primary skills necessary to begin the Task.

One teaching methodology is normally enough, and the natural sequences above already compose at runtime — the learner's outcome in one methodology is what justifies the next. Assign more than one only when the Task explicitly requires several from the start, such as a structured multi-stage session that both gates on recall and later checks unassisted performance. Do not pre-assign secondary methodologies merely because they might become useful later. Workers can discover related methodologies during execution.
