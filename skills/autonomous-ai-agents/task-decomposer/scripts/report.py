#!/usr/bin/env python3
"""report.py — the completion contract, computed by code (never LLM self-assessment).

One cycle's (plan, results) in → one report out:
  {status: complete|partial|failed,
   tasks: [{id, serves, status: done|failed|not-attempted, evidence}],
   requirements: {R-id: met|blocked|pending|waived},   # only when a dod is supplied
   delta: [records whose fp was NOT in the knowledge passed in]}

Per-task status joins plan task ids to the driver's results directly (worked→done,
failed→failed, skipped/missing→not-attempted). `status: complete` iff every asked task
is done AND (when a dod is supplied) no unmet, unwaived requirement remains pending or
blocked. The delta discipline — only knowledge not passed in comes back — is enforced
mechanically: `knowledge_in_fps` is the union of run-tier and global-tier fingerprints,
and a candidate record returns only when its fp is absent.

records_from_results() is the canonical worked/failed→record fold; relentless-solve's
harvest.harvest_tasks is a deliberate copy of it (same texts, same fp namespaces),
pinned by tests/test_contracts.py.

The dod arrives as define-done spec.py's parse_dod() output dict — this module never
imports the sibling skill; the caller loads and parses.

Stdlib only; writes only via save_report (atomic); the global knowledge tier is
READ-only.
"""

import json
import os
import re
import sys

try:
    from planfile import fp
except ImportError:  # loaded by file path (importlib) without the scripts dir on sys.path
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from planfile import fp

REPORT_BASENAME = "report.json"
_CYCLE_DIR_RE = re.compile(r"^c(\d+)$")


def report_path(cycle_dir):
    """The canonical per-cycle report artifact, beside plan.json."""
    return os.path.join(cycle_dir, REPORT_BASENAME)


def load_prior(slug_dir, global_path=None):
    """Provenance-tagged knowledge records from both tiers.

    Run tier: <slug_dir>/ledger.jsonl + every <slug_dir>/c<N>/report.json delta.
    Global tier: global_path (e.g. ${HERMES_HOME}/knowledge/global.jsonl) if present.
    Tolerant of missing files and garbage lines; never writes.
    """
    records = []

    def _jsonl(path, source):
        try:
            with open(path, encoding="utf-8") as fh:
                for ln in fh:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        rec = json.loads(ln)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if isinstance(rec, dict):
                        records.append({**rec, "source": source})
        except (FileNotFoundError, NotADirectoryError):
            pass

    _jsonl(os.path.join(slug_dir, "ledger.jsonl"), "run")
    try:
        cycles = sorted((int(m.group(1)), n) for n in os.listdir(slug_dir)
                        if (m := _CYCLE_DIR_RE.match(n)))
    except (FileNotFoundError, NotADirectoryError):
        cycles = []
    for _, name in cycles:
        try:
            with open(os.path.join(slug_dir, name, REPORT_BASENAME),
                      encoding="utf-8") as fh:
                rep = json.load(fh)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        for rec in (rep.get("delta") or []) if isinstance(rep, dict) else []:
            if isinstance(rec, dict):
                records.append({**rec, "source": "run"})
    if global_path:
        _jsonl(global_path, "global")
    return records


def knowledge_fps(records):
    """Union of fingerprints across tiers — the delta baseline."""
    out = set()
    for r in records or []:
        f = r.get("fp") or (fp(r["text"]) if r.get("text") else None)
        if f:
            out.add(f)
    return out


def records_from_results(plan, results, cycle):
    """The canonical fold of one cycle's per-task verdicts into ledger-shaped records
    (kind=dead-end fp'd on the method label; kind=fact fp'd in the "ok <method>"
    namespace; optional `learnings` each become one fact fp'd on their own text;
    skipped tasks fold nothing — the failed dependency already recorded the dead-end).
    """
    criteria = {t["id"]: t.get("success_criterion", "")
                for t in plan.get("tasks") or []}
    records = []
    for r in results:
        meta = {"task": r["id"], "criterion": criteria.get(r["id"], "")}
        if r["verdict"] == "failed":
            records.append({"cycle": cycle, "source": "report", "kind": "dead-end",
                            "text": f"Tried {r['method']}: failed — {r['evidence']}",
                            "fp": fp(r["method"]), "meta": meta})
        elif r["verdict"] == "worked":
            records.append({"cycle": cycle, "source": "report", "kind": "fact",
                            "text": f"Done {r['method']}: {r['evidence']}",
                            "fp": fp("ok " + r["method"]), "meta": meta})
        for learning in r.get("learnings") or []:
            records.append({"cycle": cycle, "source": "report", "kind": "fact",
                            "text": learning, "fp": fp(learning),
                            "meta": {**meta, "learning_from": r["id"]}})
    return records


def completion_report(plan, results, dod_parsed=None, knowledge_in_fps=(), cycle=0):
    """The completion contract for one cycle. plan = the (possibly driver-receipted)
    plan dict; results = [{id, method, verdict, evidence, learnings?}] as the driver
    produced them (a task with no result row is not-attempted); dod_parsed = define-done
    parse_dod() output or None; knowledge_in_fps = every fingerprint passed IN this
    round (both tiers) — the delta returns only records absent from it."""
    by_id = {r["id"]: r for r in results or []}

    tasks = []
    for t in plan.get("tasks") or []:
        r = by_id.get(t["id"])
        verdict = (r or {}).get("verdict")
        if verdict == "worked":
            status, evidence = "done", (r.get("evidence") or "")
        elif verdict == "failed":
            status, evidence = "failed", (r.get("evidence") or "")
        else:  # skipped, or no result row at all
            status, evidence = "not-attempted", ""
        tasks.append({"id": t["id"], "serves": list(t.get("serves") or []),
                      "status": status, "evidence": evidence})

    serving = {}
    for t in tasks:
        for rid in t["serves"]:
            serving.setdefault(rid, []).append(t["status"])

    requirements = {}
    unmet_pending = False
    for g in (dod_parsed or {}).get("groups", []):
        for it in g.get("items", []):
            rid, gid = it["id"], g.get("id")
            if it.get("marker") == "~":
                requirements[rid] = "waived"
                continue
            if it.get("marker") == "✓":
                requirements[rid] = "met"
                continue
            statuses = (serving.get(rid) or []) + (serving.get(gid) or [])
            if "done" in statuses:
                requirements[rid] = "met"
            elif statuses and all(s == "failed" for s in statuses):
                requirements[rid] = "blocked"
                unmet_pending = True
            else:
                requirements[rid] = "pending"
                unmet_pending = True

    any_done = any(t["status"] == "done" for t in tasks)
    all_done = bool(tasks) and all(t["status"] == "done" for t in tasks)
    status = ("complete" if all_done and not unmet_pending
              else "failed" if not any_done else "partial")

    knowledge_in_fps = set(knowledge_in_fps or ())
    delta = [r for r in records_from_results(plan, results or [], cycle)
             if r["fp"] not in knowledge_in_fps]
    report = {"status": status, "tasks": tasks, "delta": delta}
    if dod_parsed is not None:
        report["requirements"] = requirements
    return report


def save_report(cycle_dir, report):
    """Atomic write of report.json beside the cycle's plan.json (run tier only)."""
    os.makedirs(cycle_dir, exist_ok=True)
    path = report_path(cycle_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path
