---
# AGENT SKILLS STANDARD FIELDS (v2)
name: research
description: "Decomposition methodology for research and information-gathering work."

# KERNELAI CANONICAL FIELDS
id: supervisor.research
related: []

# UPSTREAM / ADDITIONAL FIELDS
domain: supervisor
---

# Research Decomposition

Research work is decomposed by information objective, not by query, source, or
search round. A Worker owns retrieval within one objective: choosing queries,
choosing sources, reconciling evidence, and deciding when its assigned
objective is adequately answered. The Supervisor owns breadth across
objectives and the final synthesis.

## The boundary question

For each information objective the Request names, ask:

1. Is this one information objective, or merely about the same subject as
   another?
2. Can it be researched substantially independently?
3. Would its outcome be independently useful to the final answer?
4. Would splitting substantially duplicate the same retrieval and reasoning?
5. Does it require another objective's result before research can begin?

Independent, independently useful, little duplication → separate Tasks.
Tightly coupled, substantially the same retrieval → keep together.
Dependency → sequence.

## Separate Tasks are appropriate when

- The Request names several substantial dimensions that each need their own
  retrieval and reasoning and each produce evidence useful on its own.
- A different entity requires substantial independent evidence gathering and
  yields a meaningful standalone evidence set.
- Sides of a substantial comparison each need independent retrieval under
  criteria fixed in advance.
- A claim must be verified independently of the answer it appears in.
- An information need determines what later work should be attempted.

### Example

Request: summarize Acme's major products, current availability, and recent
announcements.

- Task A: identify Acme's major current products and their roles.
- Task B: determine current availability.
- Task C: identify the most important recent announcements.

These can run concurrently; the Supervisor synthesizes them into one answer.

## Same subject is not a reason to merge

Objectives do not belong in one Task merely because they concern the same
company, product family, technology, event, or subject. Shared subject is not
shared information objective.

Likewise, some shared sources are normal and do not justify merging. Only
substantially duplicated retrieval and reasoning does.

## One final answer is not a reason to merge

A Request may require one concise final answer while still benefiting from
several independent research Tasks. The number of user-facing answers does
not determine the number of research Tasks. Combining outcomes belongs to the
Supervisor.

## Keep inside one Task

- Dimensions that form one tightly coupled fact pattern likely answered from
  the same evidence: a product's current price and stock status, or a release
  date and launch price.
- A small comparison over shared evidence or a few entities.
- Follow-up questions that refine the same objective.
- All queries, sources, and retrieval rounds one objective needs.
- Reconciling sources that disagree about the same fact.

## Replacement Tasks are not extra retrieval

A Worker owns retrieval for its objective, including deciding when the evidence
it holds is sufficient. A methodology may therefore declare a retrieval budget,
and a Worker that has spent it is expected to complete its Task with the
evidence it gathered and state what it could not verify.

Do not create a replacement research Task merely to continue retrieval for an
objective whose previous Worker exhausted its methodology retrieval budget. A
fresh Task must represent a genuinely distinct information objective, not
additional retrieval capacity for an existing objective.

These are not new information objectives when they merely continue the original
Task:

- verify further,
- search deeper,
- find another source,
- corroborate this,
- get more confidence,
- look for a more precise date,
- retry the same retrieval objective.

A blocked research Task is not a gap in the plan to be filled by another Task.
It is resumable work with preserved evidence: resolve it, continue it, or
terminate it so its findings still contribute to the answer. Recreating it as a
fresh Task would bypass both the Worker's judgment and any retrieval budget its
methodology declares.

## Legitimate follow-up research

Genuine follow-up Tasks remain correct when earlier evidence creates a new
information objective:

- a finding reveals a distinct entity the Request explicitly requires
  investigating,
- an established fact creates a new dependency the Request asks to pursue,
- a comparison becomes possible only once its sides are known under the same
  criteria.

The distinction is:

NEW SEMANTIC OBJECTIVE → potentially a new Task.

MORE RETRIEVAL FOR THE SAME OBJECTIVE → not a new Task.

## Natural dependencies

- A conclusion that depends on what is found cannot be planned before that
  finding exists.
- Work whose shape depends on external facts waits for the objective that
  establishes them.
- A comparison depends on its sides being established under the same criteria.

## Supervisor synthesis

- Cross-Task comparison and the final answer are the Supervisor's work, not
  another Task.
- Load several independent outcomes into one coherent response rather than
  asking one Worker for several objectives.

## Scope discipline

- Discovered topics are not new objectives.
- One query, one source, or one search round is not one Task.
- Do not create a Task per query, per source, or per round, and do not
  mechanically create one Task per entity.
- Do not turn a broad Request into exhaustive coverage. The requested breadth
  is the boundary.

This methodology plans Tasks. It grants no domain, execution tool, or Worker
skill.
