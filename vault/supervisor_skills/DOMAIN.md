# Supervisor Methodology Index

Supervisor methodologies are planning guidance for the Supervisor role. They
never grant Task domains, execution tools, Worker skills, or any other
authority.

Two kinds of methodology are advertised here:

- domain decomposition methodologies, which describe how work in a semantic
  domain should be decomposed into Tasks;
- context methodologies, which describe how historical Session information is
  allocated into Task instructions.

## Domain Decomposition Methodologies

### [[supervisor_skills/coding/coding|supervisor.coding]]

Decomposition methodology for software-engineering work.

### [[supervisor_skills/research/research|supervisor.research]]

Decomposition methodology for research and information-gathering work.

### [[supervisor_skills/teaching/teaching|supervisor.teaching]]

Decomposition methodology for learning and teaching interactions.

### [[supervisor_skills/data/data|supervisor.data]]

Decomposition methodology for structured-data analysis.

## Context Methodology

### [[supervisor_skills/context-allocation/context-allocation|supervisor.context-allocation]]

Context-allocation methodology for carrying relevant historical Session
information into self-contained Tasks.

This is not a work domain. It proposes no Task boundaries and authorizes no
domain. The methodologies above answer how work in a domain is divided; context
allocation answers what history the current Request needs in order to be
delegated at all. It is advertised here, and loadable like any other
methodology, so the Supervisor can consult it when a Request depends on earlier
conversation.

## Routing Principle

Load a methodology only when it clearly applies to the Request.

A Supervisor domain methodology is not Worker domain authorization: loading
one changes how the Request is planned, never what a Task is authorized to do.

Context allocation follows the same authority rule. It also does not replace a
Task's own domain decomposition methodology: both can apply to the same
Request.
