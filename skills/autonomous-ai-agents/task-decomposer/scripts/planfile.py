#!/usr/bin/env python3
"""planfile.py — the plan-as-data contract: schema constants, validation, load/dump.

A plan.json is ONE cycle's task plan, produced by a task-decomposer oneshot from
(immutable intent + evidence ledger) and consumed by a driver that attempts each task
and folds worked/failed back into the ledger. The schema is deliberately small:

  {"schema": 2, "slug": "...", "cycle": 0,
   "disposition": "tasks" | "needs_decision" | "exhausted",
   "rationale": "<one line: why this decomposition given the evidence>",
   "question": null | "<the human fork, when disposition=needs_decision>",
   "tasks": [{"id": "t1", "method": "<anti-flap identity — the approach's label>",
              "description": "<imperative, self-contained, one agent turn>",
              "success_criterion": "<observable check the executor verifies>",
              "intent_link": "<one line: why THIS task, given the evidence, is the "
                              "best-available next step toward the intent>",
              "depends_on": [], "status": "pending"}]}

Semantics the validator enforces:
  - disposition=tasks          → 1..MAX_TASKS tasks
    disposition=needs_decision → a non-empty question, no tasks (the GUARD-HALT analog)
    disposition=exhausted      → no tasks (the EXHAUSTION-STOP analog)
  - task ids are unique and machine-safe (they become durable-execution step keys)
  - depends_on refers only to EARLIER ids — a DAG by construction, executable as a
    plain list walk
  - the model always emits status "pending"; only a driver writes worked/failed/skipped
    back (validate(..., emitted=False) accepts such a receipt)
  - "intent_link" keeps the intent/mechanics boundary in the SCHEMA, not just prose
    convention: description is the imperative instruction handed to the executor,
    intent_link is the separate "why this, why now" channel back to the intent (distinct
    from the plan-level "rationale", which explains the whole decomposition, not one
    task) — this is what a relentless-solve caller's staleness gate can key on without
    an LLM call.

Optional requirements traceability (a definition-of-done in front of the intent): each
task MAY carry "serves": ["R1.2", ...] — the requirement ids it helps satisfy. The base
validator only shape-checks it; coverage_violations()/dead_violations() are the binding
checks a driver opts into, returned in validate()'s violation-string shape so
envelope.retry_suffix can echo them.

Optional decision record: the plan MAY carry a top-level "alternatives":
[{"method", "why_not_now"}, ...] — the approaches the planner actively weighed and did
not choose (see envelope._ALTERNATIVES_RULE). Advisory capture only: validate() ignores
the field entirely — a plan is NEVER rejected over it — and consumers (relentless-solve's
journey fold) read it tolerantly, dropping malformed entries.

Stdlib only; no env reads, no file writes outside dump(), no LLM.
"""

import hashlib
import json
import os
import re

SCHEMA_VERSION = 2  # v2: adds the required per-task "intent_link" field
DISPOSITIONS = ("tasks", "needs_decision", "exhausted")
STATUSES = ("pending", "worked", "failed", "skipped")
MAX_TASKS = 12
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,15}$")
PLAN_BASENAME = "plan.json"

_TASK_REQUIRED = ("id", "method", "description", "success_criterion", "intent_link")


def fp(text):
    """Anti-flap fingerprint: case/whitespace/punctuation-insensitive identity hash.

    The canonical identity for a task's "method" across cycles (and for ledger
    records generally). relentless-solve's harvest.fp is a deliberate copy of this,
    pinned behaviorally by tests/test_contracts.py — keep them in lockstep.
    """
    t = re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
    return hashlib.sha256(t.encode()).hexdigest()[:16]


def plan_path(cycle_dir):
    """The canonical artifact location for one cycle's plan."""
    return os.path.join(cycle_dir, PLAN_BASENAME)


def result_path(cycle_dir, task_id):
    """The canonical per-task verdict artifact the executor oneshot writes."""
    return os.path.join(cycle_dir, f"result-{task_id}.json")


def validate(obj, emitted=True, forbidden_ids=None):
    """Return a list of violation strings (empty = valid).

    emitted=True validates a MODEL-emitted plan (every status must be "pending");
    emitted=False validates a driver receipt (any known status allowed).
    forbidden_ids: ids a mid-cycle PARTIAL replan must not reuse (already attempted
    this cycle) — None/() is a no-op, so ordinary whole-cycle plans are unaffected.
    """
    if not isinstance(obj, dict):
        return ["plan must be a JSON object"]
    v = []
    forbidden_ids = forbidden_ids or ()
    if obj.get("schema") != SCHEMA_VERSION:
        v.append(f'"schema" must be {SCHEMA_VERSION}')
    disposition = obj.get("disposition")
    if disposition not in DISPOSITIONS:
        v.append(f'"disposition" must be one of {list(DISPOSITIONS)}')
    tasks = obj.get("tasks")
    if not isinstance(tasks, list):
        v.append('"tasks" must be a list')
        tasks = []
    if disposition == "needs_decision":
        if not (isinstance(obj.get("question"), str) and obj["question"].strip()):
            v.append('disposition "needs_decision" requires a non-empty "question"')
        if tasks:
            v.append('disposition "needs_decision" must carry no tasks')
    elif disposition == "exhausted":
        if tasks:
            v.append('disposition "exhausted" must carry no tasks')
    elif disposition == "tasks":
        if not tasks:
            v.append('disposition "tasks" requires at least one task')
        if len(tasks) > MAX_TASKS:
            v.append(f"at most {MAX_TASKS} tasks (got {len(tasks)}) — split across cycles")
    if obj.get("rationale") is not None and not isinstance(obj.get("rationale"), str):
        v.append('"rationale" must be a string')

    seen_ids = []
    for i, t in enumerate(tasks):
        tag = f"tasks[{i}]"
        if not isinstance(t, dict):
            v.append(f"{tag} must be an object")
            continue
        for field in _TASK_REQUIRED:
            if not (isinstance(t.get(field), str) and t[field].strip()):
                v.append(f"{tag}.{field} must be a non-empty string")
        tid = t.get("id")
        if isinstance(tid, str):
            if not ID_RE.match(tid):
                v.append(f"{tag}.id {tid!r} must match {ID_RE.pattern} "
                         f"(ids become durable step keys)")
            if tid in seen_ids:
                v.append(f"{tag}.id {tid!r} duplicates an earlier task id")
            if tid in forbidden_ids:
                v.append(f"{tag}.id {tid!r} collides with an id already used earlier "
                         f"this cycle (forbidden: {sorted(forbidden_ids)})")
        deps = t.get("depends_on", [])
        if not isinstance(deps, list):
            v.append(f"{tag}.depends_on must be a list")
        else:
            for d in deps:
                if d not in seen_ids:
                    v.append(f"{tag}.depends_on {d!r} must reference an EARLIER task id")
        status = t.get("status", "pending")
        if emitted:
            if status != "pending":
                v.append(f'{tag}.status must be "pending" (a planner never pre-judges)')
        elif status not in STATUSES:
            v.append(f"{tag}.status must be one of {list(STATUSES)}")
        serves = t.get("serves")
        if serves is not None and (
                not isinstance(serves, list)
                or not all(isinstance(s, str) and s.strip() for s in serves)):
            v.append(f"{tag}.serves must be a list of non-empty requirement-id strings")
        if isinstance(tid, str):
            seen_ids.append(tid)
    return v


def coverage_violations(plan, unmet_ids, known_ids=None):
    """Binding coverage check when a definition-of-done fronts the intent.

    Every unmet requirement id must be served by at least one task — unless the plan
    honestly declares "needs_decision"/"exhausted" instead. When known_ids is given,
    serves entries must also reference real requirement ids (no danglers). Same
    violation-string shape as validate(), so retry_suffix can echo the misses.
    """
    if plan.get("disposition") != "tasks":
        return []
    v = []
    served = set()
    for i, t in enumerate(plan.get("tasks") or []):
        for s in (t.get("serves") or []) if isinstance(t, dict) else []:
            served.add(s)
            if known_ids is not None and s not in known_ids:
                v.append(f'tasks[{i}].serves {s!r} references no requirement id in the '
                         f"definition of done")
    for rid in unmet_ids or ():
        if rid not in served:
            v.append(f'unmet requirement {rid} is served by no task — cover it or use '
                     f'disposition "exhausted"')
    return v


def dead_violations(plan, dead_fps):
    """Binding no-re-attempt check: a task whose method fingerprints into the known
    dead-end set is the flap the prompt's 'Dead ends' rule forbids — make it a
    validation failure, not a convention."""
    v = []
    for i, t in enumerate(plan.get("tasks") or []):
        method = t.get("method") if isinstance(t, dict) else None
        if isinstance(method, str) and fp(method) in (dead_fps or ()):
            v.append(f"tasks[{i}].method {method!r} matches a method already listed "
                     f"under 'Dead ends' — never re-propose a dead method")
    return v


def load(path):
    """Parsed plan dict, or None when absent/unparseable (caller decides the fallback)."""
    try:
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
    except (FileNotFoundError, NotADirectoryError, json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def dump(obj, path):
    """Atomic write (tmp + replace), pretty-printed for the human audit trail."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, indent=2) + "\n")
    os.replace(tmp, path)
