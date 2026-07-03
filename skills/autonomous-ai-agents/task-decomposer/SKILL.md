---
name: task-decomposer
description: >
  Use when a node of intention needs a fresh, explicit task plan as data: given an immutable
  intent plus the accumulated state of previous attempts (established facts, known gaps, dead
  ends), one `hermes -z` oneshot emits a validated plan.json — ordered, oneshot-sized tasks
  with success criteria, or an honest "needs_decision" / "exhausted" verdict. A driver (e.g.
  relentless-solve) attempts each task, records worked/failed, folds the results back into the
  state, and asks again. Triggers: "plan the next attempt", "turn this intent into tasks",
  "replan given what failed".
version: 0.1.0
author: agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    category: autonomous-ai-agents
    tags: [planning, replan, plan-as-data, oneshot, tasks, requirements-traceability]
    related_skills: [relentless-solve, method-explorer, resumable-script, define-done]
---

# Task Decomposer — (intent + attempt state) → plan.json

## Overview

The plan-generation half of a replan loop, factored out as its own skill so any driver can
swap it in. The contract is deliberately narrow:

- **Input**: a rendered body — the immutable intent verbatim, followed by the evidence
  sections a driver maintains (`## Established facts`, `## Known gaps`,
  `## Dead ends — do NOT re-attempt these methods`).
- **Invocation**: exactly one `hermes -z` oneshot per request, framed by
  `scripts/envelope.py:plan_prompt(body, out_path)`. The model writes the plan to
  `out_path` AND echoes it (artifact preferred; stdout is the fallback for torn runs).
- **Output**: one `plan.json` conforming to `scripts/planfile.py` (schema 2):

```json
{"schema": 2, "slug": "...", "cycle": 0,
 "disposition": "tasks" | "needs_decision" | "exhausted",
 "rationale": "why this decomposition given the evidence",
 "question": null,
 "tasks": [{"id": "t1", "method": "<approach label — its identity across cycles>",
            "description": "<imperative, one agent turn>",
            "success_criterion": "<strict, objectively-checkable observable check>",
            "intent_link": "<why THIS task is the best next step toward the intent>",
            "depends_on": [], "status": "pending"}],
 "alternatives": [{"method": "<an approach weighed and not chosen>",
                   "why_not_now": "<the reason, as seen at decision time>"}]}
```

Dispositions: `tasks` (1–12 ordered tasks) · `needs_decision` (a genuine human fork —
`question` set, no tasks) · `exhausted` (every viable method is already a dead end).
`alternatives` (OPTIONAL, ≤3) is the plan's PROSPECTIVE decision record — the other
approaches genuinely on the table, captured at decision time with the contemporaneous
reason they lost. It is advisory only: `validate()` ignores it entirely (a plan is never
rejected over it); relentless-solve's journey fold reads it tolerantly and renders the
entries as `not_taken` options, which is what lets its post-success hindsight pass
distinguish "saw it and passed" from "nobody saw it".
`intent_link` keeps the intent/mechanics boundary in the schema itself — `description` is
the executor's literal instruction, `intent_link` is the separate "why this, why now"
channel back to the intent (distinct from the plan-level `rationale`, which explains the
whole decomposition, not one task). For migration/cutover-shaped intents, the prompt also
offers an illustrative (non-mandatory) prepare→build→data-migrate→deploy→switchover
decomposition pattern — see `envelope.py`'s `_DECOMPOSITION_SUGGESTION`.

**Not to be confused with `method-explorer`**: this skill is a stateless, schema-owning
oneshot — it emits ONE `plan.json` and returns; it never executes anything or maintains
its own search state. `method-explorer` is the opposite shape: a self-contained,
standalone driver with its own durable plan-tree, its own AND/OR backtracking search, and
its own execution loop. `relentless-solve` uses both, for different situations — see
`method-explorer`'s SKILL.md for the full comparison.

## The two load-bearing rules

1. **Tasks are oneshot-sized** — each completable by a single agent turn under an
   iteration cap; the planner splits anything larger.
2. **The final task is a verification task** whose `success_criterion` restates the
   intent's overall success condition — so a driver may equate "all tasks worked" with
   SUCCESS in pure code, keeping LLMs out of control flow.

## Driver contract

- Validate with `planfile.validate(obj)` (returns a violation list). On violations,
  re-invoke with `envelope.retry_suffix(violations)` appended — bounded retries, then fail
  loudly.
- `method` is the anti-flap identity: fingerprint dead ends on it (not on the failure
  reason) so a replanned method that already died is suppressed by the driver's seen-set.
- The model always emits `status: "pending"`; only the driver writes
  `worked/failed/skipped` back (a receipt validates with `emitted=False`).
- Per-task verdicts live beside the plan: the executor oneshot writes
  `result-<id>.json` → `{"verdict": "worked"|"failed", "evidence": "...",
  "learnings": ["...", ...]}` (`learnings` optional; paths via `planfile.result_path`).
  A missing/malformed result file is a `failed`.
- `planfile.dead_violations(plan, dead_fps)` makes the "never reuse a dead method"
  prompt rule binding at validation time — fold it into the same retry-echo loop.

## Optional: a definition of done in front of the intent

When the caller has a requirements spec (the `define-done` skill's `dod.md`), the
decomposition becomes requirement-traced:

- Pass the unmet leaf ids to `envelope.plan_prompt(body, out_path, dod_ids=[...])`
  (and `partial_replan_prompt`): the prompt then requires a per-task
  `"serves": ["R1.2", ...]` field — the requirement ids each task helps satisfy
  (the final verification task serves the ids it verifies).
- Enforce with `planfile.coverage_violations(plan, unmet_ids, known_ids=...)`:
  every unmet id must be served by ≥1 task, serves ids must be real, and the honest
  outs remain `needs_decision`/`exhausted`. Same violation-string shape as
  `validate()`, so `retry_suffix` echoes misses.
- The dod always travels as define-done `spec.py:parse_dod()`'s **parsed dict** —
  nothing here imports the sibling skill (pinned by `tests/test_contracts.py`).

## The completion contract (`scripts/report.py`)

One cycle's outcome, computed by code — never LLM self-assessment:

```
completion_report(plan, results, dod_parsed=None, knowledge_in_fps=(), cycle=0) →
  {status: complete|partial|failed,            # complete iff EVERY task worked
   tasks: [{id, serves, status: done|failed|not-attempted, evidence}],
   requirements: {R-id: met|blocked|pending|waived},   # only when dod_parsed given
   delta: [records whose fp was NOT passed in]}
```

The **delta discipline** is mechanical: `knowledge_in_fps` is the union of everything
the round was given — run tier (`report.load_prior(slug_dir)`: the slug's
`ledger.jsonl` + prior `c<N>/report.json` deltas) and global tier
(`${HERMES_HOME}/knowledge/global.jsonl`, provenance-tagged `source: global`,
READ-only) — and only records absent from it come back. `records_from_results` is the
canonical worked/failed→ledger-record fold (dead-ends fp'd on the method label, worked
facts in the `"ok <method>"` namespace, learnings on their own text);
relentless-solve's `harvest.py` is a deliberate copy of it, pinned behaviorally by
`tests/test_contracts.py`. `save_report(cycle_dir, report)` lands `report.json`
beside `plan.json`.

Absorbed from the retired `intent-to-tasks` skill (2026-07-02) — its taskmap.md
grammar (plan.json is the single rendering now) and `alt:` OR-groups (alternative-
method search belongs to method-explorer, reachable via relentless-solve's LEVEL 2
delegation) were dropped by design; `serves`/coverage, the completion contract, and
two-tier prior state live on here.

## Resolution (for callers)

| What | Default | Override |
|---|---|---|
| `scripts/planfile.py` + `scripts/envelope.py` + `scripts/report.py` | sibling `../task-decomposer/scripts` → `${HERMES_HOME}/skills/task-decomposer/scripts` | `TASK_DECOMPOSER_DIR` |

Load all three modules from the same directory so schema, prompt, and report cannot
drift.

## Tests

```bash
python3 tests/test_planfile.py    # pure validation + envelope wording; no container
python3 tests/test_report.py     # the completion contract + two-tier prior state
python3 tests/test_contracts.py  # define-done / relentless-solve seams (skip-if-absent)
```

The primary consumer is `relentless-solve` (its `PlanContract` in
`relentless-solve/tests/test_contracts.py` pins this seam from the caller's side).
