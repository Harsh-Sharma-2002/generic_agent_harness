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
search round. A Worker already chooses queries, runs searches, weighs
evidence, and decides when it can answer; the Supervisor decides how many
distinct questions the Request actually contains.

## Independent objectives

- A question about a different entity.
- A question about a different aspect that does not share the evidence needed
  for another aspect.
- Sides of a comparison when each side needs substantially different evidence
  and the criteria can be fixed in advance.
- A claim that must be verified independently of the answer it appears in.
- An information need that determines what other work should be attempted.

## Keep inside one Task

- Several dimensions named by one Request whose evidence overlaps and whose
  answer is a single synthesis.
- A comparison over shared evidence or over a small number of entities.
- Follow-up questions that refine the same objective.
- Any number of searches, sources, or retrieval rounds one objective needs.
- Reconciling sources that disagree about the same fact.
- Answering one assigned objective at the breadth requested.

## Natural dependencies

- A conclusion that depends on what is found cannot be planned before that
  finding exists.
- Work whose shape depends on external facts waits for the information
  objective that establishes them.
- Comparison depends on each side being established under the same criteria.

## Useful parallelism

- Independent entities, independent questions, and independent verification
  targets can be researched concurrently.
- Breadth across separate objectives belongs here, not inside one Worker's
  search loop.
- Retrieval that is unrelated to local work can proceed alongside it.

## Supervisor synthesis

- Combining separately researched answers into one response is the
  Supervisor's work, not another Task, unless the Request asks for a further
  investigation that the combined evidence makes possible.
- Requests that name several required dimensions but one answer usually need
  one Task, not one per dimension.

## Scope discipline

- Discovered topics are not new objectives.
- Do not create a Task per source, per query, or per round.
- Do not turn a broad Request into exhaustive coverage. The requested breadth
  is the boundary.

This methodology plans Tasks. It grants no domain, execution tool, or Worker
skill.
