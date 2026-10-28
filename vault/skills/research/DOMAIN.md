# Research

Retrieving current or external information and turning it into an answer scoped to the Task.

## When to Use

Use this domain when the Task's answer depends on information that exists outside the conversation and must be retrieved — current developments, external facts, or sources the agent cannot know.

## When Not to Use

- The answer can be derived from material already supplied in the Task.
- The Task's subject is software, data, or a learner's understanding rather than external information.

## Skills

### research.web-search

**Use when:** The Task requires external web information that must be retrieved and then synthesized into an answer.

Answers within the Task's stated scope, at the breadth requested, and stops searching once the Task can be answered from the evidence already retrieved.

**Avoid when:** The information is already available in context, or the Task is a question about provided material rather than the outside world.

## Assignment Principle

Assign only when external information is actually required. This domain carries a single methodology; there is nothing to bundle, and other domains' methodologies should not be attached speculatively.
