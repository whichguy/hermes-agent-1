#!/usr/bin/env python3
"""envelope.py — the canonical task-decomposer invocation contract (single source).

The prompt framing that makes a bare `hermes -z` turn act as a PLANNER: read the
immutable intent plus the rendered evidence ledger and emit ONE plan.json (see
planfile.py) — data, not execution. External callers (e.g. the relentless-solve
driver) import this module so the artifact path, schema wording, and retry framing
cannot drift from the skill.

The plan travels on TWO channels — written to the artifact path AND echoed as the
final message — because a oneshot killed on timeout often leaves empty stdout while
the file write already landed (readers prefer the artifact, fall back to stdout).

Two rules here are load-bearing for the driver's semantics:
  - tasks must be ONESHOT-SIZED (one agent turn under the iteration cap), so a
    per-task executor can attempt each one in a single `hermes -z` call;
  - the FINAL task must be a verification task restating the intent's success
    condition, so a driver may equate "all tasks worked" with overall SUCCESS in
    pure code, keeping any LLM out of the control flow.

Stdlib-only; no env reads, no imports.
"""

SCHEMA_EXAMPLE = (
    '{"schema": 2, "slug": "<slug>", "cycle": 0, "disposition": "tasks", '
    '"rationale": "<one line: why this decomposition given the evidence>", '
    '"question": null, '
    '"tasks": [{"id": "t1", "method": "<short label of the approach — its identity>", '
    '"description": "<imperative, self-contained instruction for one agent turn>", '
    '"success_criterion": "<observable check the executor must verify>", '
    '"intent_link": "<one line: which part of the intent this task advances, and why '
    'it is the best-available next step given the CURRENT evidence>", '
    '"depends_on": [], "status": "pending"}], '
    '"alternatives": [{"method": "<an OTHER viable approach you weighed and did not '
    'choose>", "why_not_now": "<one line, the reason as seen NOW>"}]}'
)

# Shared by plan_prompt AND partial_replan_prompt (one block, so the two prompts'
# decision-record contract cannot drift). The field is advisory capture, never binding:
# planfile.validate ignores it entirely — a plan is NEVER rejected over it — and the
# relentless-solve journey fold (journey.py) reads it tolerantly. Prospective capture
# is the point: options recorded AT DECISION TIME are what let a post-success hindsight
# pass distinguish "saw it and passed" from "nobody saw it".
_ALTERNATIVES_RULE = (
    '- "alternatives" (OPTIONAL — omit when there were none): up to 3 OTHER viable '
    "approaches you ACTIVELY WEIGHED for this decision and did not choose, each "
    '{"method", "why_not_now"}. This is the run\'s decision record, not brainstorming — '
    "record only options genuinely on the table, with the contemporaneous reason "
    "they lost.\n"
)

# Shared between plan_prompt (whole-cycle) and partial_replan_prompt (mid-cycle tail-only)
# so the two prompts' field rules cannot drift apart over time.
_TASK_RULES = (
    "Rules:\n"
    '- disposition "tasks": 1-12 ordered tasks. Each task must be completable by a '
    "single agent turn under an iteration cap — split anything larger.\n"
    '- Each task\'s "method" names the APPROACH (its identity across cycles); NEVER '
    "reuse a method listed under 'Dead ends'.\n"
    '- "success_criterion" must be a STRICT, OBJECTIVELY-CHECKABLE definition of done: a '
    "concrete, observable condition the executor can verify without judgment. Name the "
    "exact artifact/output/state and the exact check.\n"
    "  BAD (vague, a judgment call): \"the API works correctly\" / \"the tests pass\" / "
    "\"handles the edge case properly\".\n"
    "  GOOD (checkable): \"GET /health returns HTTP 200 with body "
    "{\\\"status\\\":\\\"ok\\\"}\" / \"pytest tests/test_auth.py exits 0 with 0 "
    "failures\" / \"config.yaml contains a top-level 'retries: 3' key, verified by "
    "reading it back\".\n"
    '- "intent_link" ties the task back to the INTENT, not to implementation mechanics — '
    "state WHAT part of the goal it advances and WHY it is the best next step now, in "
    "intent-level language. Not \"what it does\" (that's \"description\") — the causal "
    "link from evidence/dead-ends to this specific choice.\n"
    '- "depends_on" may only reference EARLIER task ids; ids match '
    '^[a-z0-9][a-z0-9-]{0,15}$; every status is "pending".\n'
    "- The text ABOVE the evidence headers (Established facts / Known gaps / Dead ends) "
    "is the immutable INTENT — declarative, logical, goal-level. Everything below it is "
    "grounded evidence from executed attempts. ALL mechanical detail (specific commands, "
    "file paths, library/tool names) belongs in a task's \"method\"/\"description\" — "
    "never invent new mechanics inside \"intent_link\" (keep it a one-line "
    "justification), and never treat evidence-section specifics as though they were "
    "part of the intent's own wording.\n"
)


# An ILLUSTRATIVE example only (plan_prompt, not partial_replan_prompt — the "chart a
# fresh happy-path outline" moment, not the "patch an already-mid-flight tail" one).
# Deliberately a suggestion, not a rule: no schema field enforces it, no validator checks
# for it, and the model is told explicitly to ignore it when the intent doesn't fit.
_DECOMPOSITION_SUGGESTION = (
    "Illustrative decomposition pattern (a SUGGESTION, not a mandate — use it when it "
    "fits, ignore it otherwise): for intents shaped like replacing a live system "
    "without downtime (a database migration, a service cutover, an infra swap), a "
    "PREPARE -> BUILD -> DATA MIGRATE (if any) -> DEPLOY -> SEAMLESS SWITCHOVER "
    "decomposition is often a strong default — each phase becomes one or more "
    "oneshot-sized tasks, and it naturally keeps \"build the new thing\" separate from "
    "\"cut traffic over to it\" (two very different failure modes deserve two different "
    "tasks). Many intents (debugging, feature work, investigation, one-off fixes) don't "
    "have this shape at all — plan whatever decomposition the evidence actually "
    "supports; do not force this pattern onto an intent it doesn't fit.\n"
)


def _dod_block(dod_ids):
    """Instruction block added when a definition-of-done fronts the intent. The tokens
    here ("serves", the id list, the cover-or-exhausted rule) are what
    planfile.coverage_violations enforces — keep wording and validator in lockstep
    (pinned by tests/test_contracts.py)."""
    if not dod_ids:
        return ""
    ids = ", ".join(dod_ids)
    return (
        "The intent carries a DEFINITION OF DONE (see its Requirements section). Add to "
        'EVERY task a "serves" field: a JSON list of the requirement ids that task helps '
        'satisfy, e.g. "serves": ["R1.2"]. Every unmet requirement id — '
        f"{ids} — must be served by at least one task (the final verification task "
        "serves the ids it verifies). If some requirement cannot be served by any "
        "method not already under 'Dead ends', do not silently omit it — use "
        'disposition "exhausted" (or "needs_decision" if a human choice would unblock '
        "it).\n"
    )


def plan_prompt(body, out_path, dod_ids=None):
    """The planning oneshot for one cycle. body = immutable intent + rendered ledger
    (Established facts / Known gaps / Dead ends sections); out_path = where plan.json
    must be written; dod_ids = unmet requirement ids when a definition-of-done fronts
    the intent (adds the serves/coverage contract)."""
    return (
        "You are a TASK PLANNER. Produce a plan as DATA — do NOT execute, research, or "
        "modify anything else. Read the intent and evidence below, then plan the best "
        "next attempt.\n\n"
        f"{body.rstrip()}\n\n"
        "Emit ONE JSON object matching this schema exactly:\n"
        f"{SCHEMA_EXAMPLE}\n"
        f"{_TASK_RULES}"
        f"{_ALTERNATIVES_RULE}"
        "- The FINAL task must be a VERIFICATION task whose success_criterion restates "
        "the intent's overall success condition.\n"
        "- If a genuine decision only a human can make blocks planning, use disposition "
        '"needs_decision" with the "question" field and no tasks.\n'
        "- If every viable method is already listed under 'Dead ends', use disposition "
        '"exhausted" with no tasks.\n'
        f"{_dod_block(dod_ids)}"
        f"{_DECOMPOSITION_SUGGESTION}"
        f"Write the JSON to {out_path} AND echo the same JSON as your final message."
    )


def partial_replan_prompt(body, out_path, forbidden_ids, dod_ids=None):
    """A MID-CYCLE replan: some of this cycle's tasks already ran; fresh evidence suggests
    the REMAINING (not-yet-attempted) tasks may no longer be the best next steps. `body`
    already carries intent + ledger + a "Completed this cycle so far" section (see the
    caller's render_partial). Unlike plan_prompt, the model is asked ONLY for the new
    tail — it never has to reproduce already-completed tasks verbatim; the driver splices
    the two lists together in code."""
    forbidden = ", ".join(sorted(forbidden_ids)) or "(none yet)"
    return (
        "You are a TASK PLANNER performing a PARTIAL REPLAN mid-cycle: some tasks in "
        "this cycle's plan already ran; new evidence suggests the REMAINING tasks may no "
        "longer be the best next steps. Replan ONLY the remaining work — do not restate "
        "or re-emit the tasks that already ran.\n\n"
        f"{body.rstrip()}\n\n"
        "Emit ONE JSON object matching this schema exactly (containing ONLY the new "
        "remaining tasks, not the ones already completed):\n"
        f"{SCHEMA_EXAMPLE}\n"
        f"{_TASK_RULES}"
        f"{_ALTERNATIVES_RULE}"
        f"- Task ids MUST NOT be any of: {forbidden} (already used earlier this cycle).\n"
        "- If the tasks already completed this cycle already satisfy the intent, emit "
        "ONE short VERIFICATION task restating the success condition.\n"
        "- If a genuine decision only a human can make blocks planning, use disposition "
        '"needs_decision" with the "question" field and no tasks.\n'
        "- If every viable remaining method is already listed under 'Dead ends', use "
        'disposition "exhausted" with no tasks.\n'
        f"{_dod_block(dod_ids)}"
        f"Write the JSON to {out_path} AND echo the same JSON as your final message."
    )


def retry_suffix(violations):
    """Appended to plan_prompt/partial_replan_prompt when a previous emission failed
    validation."""
    return ("\n\nYour previous plan FAILED validation:\n"
            + "\n".join(f"- {v}" for v in violations)
            + "\nOutput ONLY the corrected JSON object (write the file AND echo it), "
              "nothing else.")
