---
# AGENT SKILLS STANDARD FIELDS (v2)
name: coding
description: "Decomposition methodology for software-engineering work."

# KERNELAI CANONICAL FIELDS
id: supervisor.coding
related: []

# UPSTREAM / ADDITIONAL FIELDS
domain: supervisor
---

# Coding Decomposition

Software work is decomposed by the semantic outcome being pursued, not by the
steps a Worker performs. A Worker already plans its own investigation,
implementation, testing, and cleanup inside one Task; the Supervisor decides
only when the objective itself changes.

## Independent objectives

- Establishing why something fails, when the cause is genuinely unknown.
- Changing behaviour whose expected result is known.
- Establishing whether an approach is feasible before committing to a build.
- Verifying whether existing changes are correct and non-regressing.
- Improving the internal quality of changes already known to work.
- Exercising a running application and reporting what breaks.
- Measuring a repository's size and composition.
- Delivering an existing change through review, CI, and merge.
- Reviewing someone else's proposed change.

Each can stand alone as the answer to a Request. Several can also be carried
out inside one implementation Task without becoming separate objectives.

## Natural dependencies

- A change whose shape depends on an unknown cause cannot be scoped until the
  cause is established. When a Request asks for a fix and the cause is
  unknown, diagnosis is a prerequisite for deciding what the change is — not
  necessarily for performing it, since one Worker can diagnose and then fix.
- Verification, cleanup, and delivery assume an artifact exists. They cannot
  precede the change they act on.
- Delivery depends on a change that has already been verified. A Request that
  asks for both orders them; it does not merge them.
- A feasibility verdict gates a commitment to build only when the Request
  makes the build conditional on that verdict.
- A reported defect gates a fix only when the report is the evidence the fix
  depends on; otherwise the fixing Task must reproduce the defect itself.

## Keep inside one Task

- Diagnosis and the fix it justifies, when the Request asks for the failure
  to be resolved rather than explained.
- Implementation and its ordinary tests: writing a failing test, running it,
  and correcting the code is one objective, not several.
- An implementation and the small amount of cleanup needed to leave it
  coherent.
- Reading a repository to locate the code a change touches. Orientation is
  part of the change, not a deliverable.
- Instrumentation, debugger sessions, and log inspection: these establish
  state, they are not objectives.
- Iterating on a failing query, build, or test until the reported symptom is
  resolved.

## Useful parallelism

- Changes to genuinely disjoint components, files, or behaviours whose
  outcomes do not depend on each other.
- Independent investigations of unrelated failures.
- An exploratory QA pass over one surface while unrelated work proceeds
  elsewhere.
- Measurement of an existing repository while unrelated preparation happens.

Work that touches the same files, the same behaviour, or the same failing path
shares context and sequencing. Running it concurrently produces conflicts
rather than speed.

## Outcomes that unlock later work

- A confirmed root cause unlocks a scoped fix.
- A working, verified change unlocks delivery.
- A feasibility verdict unlocks a commitment to implement.
- A reproducible defect report unlocks a targeted fix.
- A merged change unlocks work that builds on it.

## Scope discipline

- The Request defines which outcomes are wanted. A Request for a fix does not
  implicitly request a review, a cleanup, or delivery.
- Delivery, review of others' changes, and repository measurement are separate
  objectives only when the Request names them.
- When a Request is one change to one behaviour, one Task is almost always the
  whole decomposition.
- Do not split a Request into the phases a Worker would follow internally.
  Phases are not objectives.

This methodology plans Tasks. It grants no domain, execution tool, or Worker
skill.
