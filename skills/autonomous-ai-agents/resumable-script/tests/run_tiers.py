#!/usr/bin/env python3
"""run_tiers — climb the COMPLEXITY TIERS (suites.TIERS), simplest first, stopping at the first
failing tier. Prints a compact scoreboard and writes the machine-readable ground-truth artifact
`tests/.last_run.json` so a run dispatched through the ask skill is judged by the artifact, never
by an agent's narrative (see tests/ask_run.sh).

  python3 tests/run_tiers.py                       # climb tier1 -> tier5
  python3 tests/run_tiers.py --through tier3-interrupts
  python3 tests/run_tiers.py --only tier1-basics   # any suite name works here

ARTIFACT CONTRACT
  The artifact is written on EVERY path once main()'s body runs: a normal climb, a tier failure, a
  bad --only/--through name (exit 2), and a rung that raises Exception OR SystemExit (the drift
  signal run_ladder._validate_suites() raises). It is NOT written for argparse usage/--help exits
  or for an import-time assertion in suites.py (the TIERS invariant guard) — those are invocation /
  load failures, not a run. A genuine KeyboardInterrupt still propagates (not caught).
  Shape: {v, started, finished, target, tiers: [{name, rungs, status[, error]}], overall, exit
          [, nonce]}
    overall in {"ok","failed","error"};  exit in {0,1,2}
    tier status in {"pass","FAIL","ERROR","skipped"};  "ERROR" entries carry an "error" message.
  The optional nonce is stamped from $RUN_TIERS_NONCE for provenance verification by ask_run.sh.
  Path is overridable with $RUN_TIERS_ARTIFACT (tests point it at a temp file; $TMPDIR does NOT
  move it — it lives beside this script by default).
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import run_ladder                                    # noqa: E402
from suites import SUITES, TIERS                     # noqa: E402

ARTIFACT = os.environ.get("RUN_TIERS_ARTIFACT") or os.path.join(HERE, ".last_run.json")
NONCE = os.environ.get("RUN_TIERS_NONCE")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_artifact(payload):
    tmp = ARTIFACT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, sort_keys=True)
    os.replace(tmp, ARTIFACT)


def _finish(started, plan, results, overall, exit_code, target):
    """Write the artifact (skipped tiers included) and print the scoreboard. The single exit point
    so the ground-truth artifact exists on every return path."""
    done = {r["name"] for r in results}
    for name in plan:
        if name not in done:
            results.append({"name": name, "rungs": len(SUITES.get(name, [])), "status": "skipped"})
    payload = {"v": 1, "started": started, "finished": _now(), "target": target,
               "tiers": results, "overall": overall, "exit": exit_code}
    if NONCE:
        payload["nonce"] = NONCE
    _write_artifact(payload)
    print("\n--- scoreboard ---")
    for r in results:
        extra = " — %s" % r["error"] if r.get("error") else ""
        print("%-18s %s (%d rungs)%s" % (r["name"], r["status"], r["rungs"], extra))
    print("overall: %s (artifact: %s)" % (overall, os.path.relpath(ARTIFACT, os.getcwd())))
    return exit_code


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--through", default=None, help="stop after this tier")
    ap.add_argument("--only", default=None, help="run a single suite (any suites.py name)")
    args = ap.parse_args(argv)

    started = _now()
    target = args.only or (args.through or "all-tiers")

    if args.only:
        plan = [args.only]
    elif args.through is not None and args.through not in TIERS:
        print("unknown tier: %r (tiers: %s)" % (args.through, ", ".join(TIERS)))
        return _finish(started, [], [], "error", 2, target)
    else:
        plan = TIERS if args.through is None else TIERS[:TIERS.index(args.through) + 1]

    bad = [n for n in plan if n not in SUITES]
    if bad:
        print("unknown suite: %r (known: %s)" % (bad[0], ", ".join(sorted(SUITES))))
        return _finish(started, [], [], "error", 2, target)

    results = []
    overall = "ok"
    for name in plan:
        print("=== %s (%d rungs) ===" % (name, len(SUITES[name])))
        try:
            rc = run_ladder.main(["--suite", name])
        except (Exception, SystemExit) as e:         # a drift SystemExit / a crashing rung — record it
            results.append({"name": name, "rungs": len(SUITES[name]),
                            "status": "ERROR", "error": str(e) or type(e).__name__})
            overall = "error"
            break
        results.append({"name": name, "rungs": len(SUITES[name]),
                        "status": "pass" if rc == 0 else "FAIL"})
        if rc != 0:
            overall = "failed"
            break                                    # the escalation stops at the first failure

    exit_code = 0 if overall == "ok" else 1
    return _finish(started, plan, results, overall, exit_code, target)


if __name__ == "__main__":
    sys.exit(main())
