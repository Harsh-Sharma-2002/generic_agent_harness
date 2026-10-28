# Coding

Software-development methodology: diagnosing failures, driving implementation with tests, reviewing and simplifying changes, observing a running program, prototyping feasibility, and operating repository or GitHub workflows.

## When to Use

Use this domain when a Task's objective is to investigate, change, verify, or operate software:

- reproduce and understand a defect, then fix its cause
- implement or change behaviour under test discipline
- review, verify, or clean up existing changes
- observe the internal state of a running program that passive inspection cannot explain
- exercise a running application's interface and report defects
- establish whether an approach is feasible before real implementation
- measure a repository's size and composition
- perform GitHub work: issues, pull requests, review of others' work, repository setup

## When Not to Use

- The Task is to explain a programming concept or answer a question **about** software rather than change or operate it. No coding methodology is needed.
- The Task only needs current external information. Use `research`.
- The objective is someone's own understanding or practice of a topic. Use `teaching`.
- The Task produces content (documentation, prose, analysis) with no software change. Route by the content's subject instead.

## Skills

### coding.codebase-inspection

**Use when:** The Task needs a repository measured — size, language breakdown, file counts, or code-versus-comment ratios.

Produces quantitative composition data about a codebase; it does not judge whether the code is good.

**Avoid when:** The Task is about the quality, correctness, or purpose of the code rather than its size.

### coding.dogfood

**Use when:** A running web application must be exercised end to end and the defects it exhibits reported reproducibly with captured evidence.

Drives the application as a user would, records what breaks, and produces a structured defect report rather than a fix.

### coding.github

**Use when:** The Task is GitHub work — authentication setup, issue triage and management, branch-to-pull-request delivery, watching CI, merging, or reviewing someone else's pull request.

Covers repository and pull-request operations from first commit through review and merge.

**Avoid when:** The work is local verification of your own changes before they are pushed; use `coding.requesting-code-review`.

### coding.hermes-agent-skill-authoring

**Use when:** A new or edited in-repository skill file must satisfy a repository's hardline skill-authoring standards before it is committed.

Walks the authoring standards operationally, including where a skill belongs and which tier it qualifies for.

**Avoid when:** The task is to author a KernelAI vault skill rather than a Hermes in-repo skill.

### coding.inspecting-hermes-desktop-dom

**Use when:** A change must be verified against a running Electron desktop application, and the answer is a factual question about the live rendered interface — what actually rendered, its computed styling, its geometry, console output.

Reads the running renderer's real state instead of inferring it from source.

**Avoid when:** The question is whether the result *looks* good; that needs a human or a screenshot, which this skill explicitly does not substitute for.

### coding.node-inspect-debugger

**Use when:** A Node.js or JavaScript process needs interactive inspection — breakpoints, stepping, scope and closure state, or evaluating expressions in a paused frame.

Uses the runtime's inspector to observe state that logging cannot reach without editing the source.

**Avoid when:** Logging would resolve the question in under a minute; breakpoint-driven inspection is heavier.

### coding.python-debugpy

**Use when:** A Python process needs interactive inspection — a breakpoint at a specific line, stepping through mutation, post-mortem locals at a crash, or attaching to a long-running process that cannot be restarted.

Pauses execution and exposes real interpreter state at the line that misbehaves.

**Avoid when:** The failing side of the stack is Node or JavaScript; use `coding.node-inspect-debugger`.

### coding.requesting-code-review

**Use when:** The agent's own changes are complete and must be verified before they land — a commit, push, merge, or an explicit request to verify or review before shipping.

Runs independent verification of existing changes: static security scanning, quality gates measured against a baseline, and a reviewer working from fresh context.

**Avoid when:** The changes already work and the goal is tidiness rather than confirmation; use `coding.simplify-code`.

### coding.simplify-code

**Use when:** Recent, already-working changes should be cleaned up — duplication removed, needless complexity flattened, waste cut, band-aids deepened.

A quality-cleanup pass over changes that are assumed correct, not a hunt for correctness bugs.

**Avoid when:** Correctness, security, or "did this regress anything" is in question; use `coding.requesting-code-review`.

### coding.spike

**Use when:** An idea must be felt out before committing to a real build — feasibility, approach comparison, or unknowns that research alone cannot settle.

Builds a deliberately disposable prototype whose only product is a verdict.

**Avoid when:** The answer is obtainable from documentation or reading code, the idea is already validated, or the work is on the production path.

### coding.systematic-debugging

**Use when:** Something fails and the cause is not yet understood.

Establishes a tight, reproducible failing signal, then forces diagnosis to a root cause before any fix is proposed.

**Avoid when:** The cause is already established and the remaining work is implementing the fix under test discipline; use `coding.test-driven-development`.

### coding.test-driven-development

**Use when:** Behaviour must be added, changed, or fixed with known expectations — new features, behaviour changes, refactoring, bug fixes once the cause is known.

Writes the failing test first and then only the minimal code required to make it pass.

**Avoid when:** The cause of the failure is still unknown; diagnose first with `coding.systematic-debugging`.

## Routing Distinctions

**Diagnosis versus instrumentation.** `coding.systematic-debugging` decides *what* to investigate and when a fix is justified. `coding.node-inspect-debugger` and `coding.python-debugpy` are how a paused program is observed. When the cause is unknown, the methodology is primary; a language debugger is added only when the reasoning needs state that reading and logging cannot supply. Choose the debugger by the language of the failing side.

**Verification versus cleanup.** `coding.requesting-code-review` asks whether changes are correct, safe, and non-regressing. `coding.simplify-code` assumes the changes are correct and asks whether they are well built. Both act on recent changes and both can be answered independently of each other.

**Own changes versus others' changes.** `coding.requesting-code-review` verifies the agent's own uncommitted work. `coding.github` review work is for other people's pull requests on GitHub, with inline comments and a verdict.

**Test discipline versus debugging.** `coding.test-driven-development` starts from a known desired behaviour. `coding.systematic-debugging` starts from an unexplained failure. They compose in one direction: a diagnosed bug commonly becomes a failing regression test before the fix.

**Exploratory QA versus factual inspection.** `coding.dogfood` exercises a whole web application through its interface as a user and reports what breaks. `coding.inspecting-hermes-desktop-dom` answers narrow factual questions about a running desktop renderer's state. Neither assesses visual quality.

**Measurement versus judgement.** `coding.codebase-inspection` reports size and composition. It says nothing about whether the repository is healthy, and should not be assigned as though it did.

**Throwaway versus production.** `coding.spike` is disposable by design and exists to answer a feasibility question. Anything that needs to last belongs under `coding.test-driven-development` instead.

## Assignment Principle

Prefer the smallest sufficient set of primary skills necessary to begin the Task.

One methodology is normally enough. Assign more than one only when the Task genuinely requires several from the start — for example, verifying changes and then landing them, or diagnosing a failure and then fixing it under test discipline. Do not pre-assign secondary methodologies merely because they might become useful later. Workers can discover related methodologies during execution.
