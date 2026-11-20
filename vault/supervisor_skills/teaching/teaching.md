---
# AGENT SKILLS STANDARD FIELDS (v2)
name: teaching
description: "Decomposition methodology for learning and teaching interactions."

# KERNELAI CANONICAL FIELDS
id: supervisor.teaching
related: []

# UPSTREAM / ADDITIONAL FIELDS
domain: supervisor
---

# Teaching Decomposition

Teaching work is decomposed by learning objective and by learner, not by
pedagogical technique. A Worker already sequences recall, explanation,
diagnosis, hinting, practice, and reflection inside one interaction; the
Supervisor decides when the Request asks for a different kind of learning
outcome, or a different learner.

## One interaction is normally one Task

- A session, a worked problem, a review of material, an explanation the
  learner must produce, a stuck point, a confidence check, or a transfer
  attempt are facets of one interaction.
- Techniques that would conflict if run at the same time — withholding
  instruction while the learner explores versus supplying graduated help —
  must never become concurrent Tasks for the same learner and topic.
- Techniques that measure different evidence — explanation quality,
  portability to a new context, performance without help — are sequenced
  inside an interaction rather than split across Tasks, unless the Request
  asks for one of them specifically.

## Independent objectives

- A single interactive session versus a review across a period of prior work.
- Establishing what the learner can do unaided, when that is the requested
  outcome rather than part of a session.
- Evaluating the trustworthiness of an AI-produced explanation the learner is
  relying on.
- Work with a different learner, or on a genuinely different subject, with no
  shared evidence.

## Natural dependencies

- Diagnosis of a block precedes targeted help. Help offered before the
  breakdown is understood addresses the wrong thing.
- Explanation, transfer, and unaided performance are only meaningful after
  the learner has had the opportunity to learn or practise.
- A period review depends on accumulated evidence from earlier sessions.
- Scaffolding can only be reduced on the strength of unaided performance.

## Shared context

- Teaching Tasks are stateful in the learner: confidence, hint depth, errors,
  and prior evidence carry across an interaction.
- Concurrent teaching Tasks on the same learner and topic compete over the
  same state and the same scaffolding decisions, so they are sequential or
  one Task.
- Do not fragment an interaction into a Task per technique.

## Outcomes that unlock later work

- A diagnosed misconception unlocks targeted practice.
- Demonstrated understanding unlocks transfer or independence checks.
- Unassisted success unlocks reduced support.
- Accumulated session evidence unlocks a periodic review.

## Evidence the Request must supply

- A review across sessions needs evidence of those sessions. Do not plan it
  from assumed history, and do not invent learner records.
- A claim about progress needs a prior measurement to compare against.

## Scope discipline

- The Request defines the learning objective. Do not add assessment,
  calibration, or review stages it did not ask for.
- Producing teaching material — a handout, plan, or explanation to be read —
  is content production, not a learning interaction.

This methodology plans Tasks. It grants no domain, execution tool, or Worker
skill.
