---
# AGENT SKILLS STANDARD FIELDS (v2)
name: data
description: "Decomposition methodology for structured-data analysis."

# KERNELAI CANONICAL FIELDS
id: supervisor.data
related: []

# UPSTREAM / ADDITIONAL FIELDS
domain: supervisor
---

# Data Decomposition

Data work is decomposed by analytical question, not by query. A Worker already
discovers the schema, inspects tables and relationships, runs as many read-only
queries as the question needs, validates the results, and retries; the
Supervisor decides whether the Request contains one analytical objective or
several.

## Independent objectives

- Questions about genuinely different subject areas that need different
  tables and different discovery.
- A follow-up analysis whose premise is an earlier result and whose shape
  cannot be known in advance.
- A question that must be answered from the database and a question that must
  be answered from outside it.
- Verification of a specific figure against the data, independent of the
  analysis that produced it.

## Keep inside one Task

- Schema discovery, table and column inspection, relationship discovery,
  multiple queries, and result validation for one question.
- Several related measures, breakdowns, or filters over the same entities and
  the same tables.
- Refining a query that returned an unexpected, empty, or failed result.
- Any number of queries, joins, or retries one analytical objective requires.
- Clarifying an ambiguous question, once the Supervisor has stated which
  interpretation is meant.

## Natural dependencies

- An analysis that builds on another's result depends on that result.
- A comparison or aggregate across separately produced answers depends on
  those answers.
- Verification depends on an existing figure or claim.
- A question whose meaning depends on an external fact waits for that fact.

## Shared context

- Discovery happens per Task and is not shared automatically, so questions
  over the same tables are usually cheaper and more consistent together.
- Questions that must agree with one another — the same population, period,
  or definition — belong together or must be given identical definitions.
- Splitting related measures across Tasks risks inconsistent definitions and
  duplicated discovery.

## Useful parallelism

- Independent subject areas with no shared tables or definitions.
- Analysis of one area alongside unrelated retrieval of external context.

## Supervisor synthesis

- Combining figures from separate analyses into one answer is the
  Supervisor's work.
- Do not create a Task per query, per table, or per metric.

## Scope discipline

- State the analytical question precisely enough that the Worker does not
  have to choose between materially different interpretations.
- Ask only for the data the Request needs. Do not expand an analysis into
  adjacent metrics.

This methodology plans Tasks. It grants no domain, execution tool, or Worker
skill.
