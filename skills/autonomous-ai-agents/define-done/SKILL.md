---
name: define-done
description: >
  Use when a prompt should be compiled into a requirements / definition-of-done spec before
  anything is planned or executed: it decomposes the intent into grouped, itemized, logically
  ordered OUTCOMES that must hold at the end (what, never how), each optionally carrying an
  acceptance check, written as a durable dod.md artifact that planners, mappers, and checkers
  consume. Triggers: "define done for this", "what are the requirements", "spec this before
  solving", "decompose this into a definition of done".
version: 0.1.0
author: agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    category: autonomous-ai-agents
    tags: [requirements, definition-of-done, intent, specification, world-state]
    related_skills: [task-decomposer, relentless-solve, method-explorer, next-best-questions]
---

# Define Done — compile a prompt into a requirements spec

## Overview

Given an intent, produce `${HERMES_HOME}/specs/<slug>/dod.md`: the definition of done.
You are the SPECIFIER — you decide **what must be true**, never **how to make it true**.
Do not plan methods, do not execute anything, do not research beyond what the prompt and
provided evidence contain (unresolvable ambiguities go under `OPEN:`).

## The world-state test (apply to every item)

Every requirement must be phrased as a **condition that IS TRUE at the end**, checkable
by observation — never an activity.

| FAILS the test (activity) | PASSES (world-state) |
|---|---|
| Run the migration | every row in `users` has non-null `tenant_id` |
| Write unit tests for the parser | the parser has a test suite that exercises each grammar rule and passes |
| Deploy the service | the service answers `GET /health` with 200 from the public URL |
| Fix the login bug | a user with valid credentials reaches the dashboard; the regression test for issue #N passes |

If an item starts with a verb aimed at a tool, rewrite it as the state the verb would
produce. Group items by logical stage of world-state; order groups with `[after:]` by
**dependency** (this must hold before that *can* hold), not by execution sequence.

## STEP 1 — Write the artifact, exactly in this shape

```markdown
# DoD: <slug>   STATE: draft | agreed | satisfied
INTENT: <one sentence — the outcome that must hold; immutable, never edit it>
HARD (inviolable): <list>
SOFT (relaxable, ranked): 1) ...  2) ...

REQUIREMENTS   (markers: ○ unmet · ✓ met (receipt) · ~ waived (receipted reason))
- R1   <group: outcome that must hold>              [after: —]
  - R1.1  <itemized requirement>   check: cmd — <command that proves it>    ○
  - R1.2  <itemized requirement>   check: judge — <observable criterion>    ○
  - R1.3  <itemized requirement>                                            ○
- R2   <group>                                       [after: R1]
OPEN: <ambiguities you could not resolve — one line each; empty is fine>
AMENDMENTS:
```

- **Checks are optional** per leaf: `check: cmd — <command>` when a command can prove it;
  `check: judge — <criterion>` when only an observable criterion exists; bare when
  neither. Know the cost of bare: a bare or judged item may later be marked ✓ ONLY with
  a receipt stating what was observed.
- Fresh specs start `STATE: draft` with every leaf `○`. A human (or the calling loop's
  policy) moves draft → `agreed`; `satisfied` is set only when every non-waived leaf is
  ✓ with a receipt.

## STEP 2 — Amending (never rewriting)

`INTENT:` is immutable. Requirements change ONLY by appending to `AMENDMENTS:` with a
one-line receipted reason, then applying the change in place:

```
AMENDMENTS:
- c2 R2.3 waived — source system has no audit table; requirement unsatisfiable as stated
- c2 R2.4 added — discovered consumers cache the old schema; invalidation must also hold
```

Waived leaves get marker `~` with the reason after it. Never delete a leaf; never edit
INTENT; never mark ✓ without a receipt.

## What this skill is NOT

No methods, tasks, tools, or plans — that is the `task-decomposer` skill (whose plans
trace back here via per-task `serves: [R-ids]` when a driver passes the unmet ids in).
No execution — that is the method-explorer. No research loops — unresolved questions
go to `OPEN:` (downstream clarify rounds rank them). The value of this artifact is that
it stays stable while methods fail and change underneath it.
