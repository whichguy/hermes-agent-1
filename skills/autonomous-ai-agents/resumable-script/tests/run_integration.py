#!/usr/bin/env python3
"""Real-repo integration for the investigation flow (no fixtures, no LLM).

Runs examples/investigate_repo in REAL backend against a copy of a planted-bug repo
(tests/fixtures/broken_project): real repo scan, real `python3 run_tests.py`, real grep,
a real file edit, and a real re-run that goes green. Proves the durable flow works on
actual source — not just canned fixtures. Kept OUT of the hermetic ladder (it shells out
and touches the filesystem); run it explicitly or via `tests/run.py --with-integration`.

    python3 tests/run_integration.py [--engine py|js|both]

Scenarios (per engine):
  A  broken env -> reproduce fails -> fix env, resume (map memoized) -> pick suspect ->
     approve -> real edit -> real re-run passes  (command-fails->fix->resume, end to end)
  B  crash mid real-edit -> in-doubt -> resolve completed -> verify passes (durability)
  C  crash mid real-edit -> in-doubt -> resolve RETRY (safe re-apply) -> verify passes
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE_PY = os.path.join(ROOT, "scripts", "engine.py")
ENGINE_JS = os.path.join(ROOT, "extras", "js-mirror", "scripts", "engine.js")  # quarantined mirror
FLOW_PY = os.path.join(ROOT, "examples", "investigate_repo.py")
FLOW_JS = os.path.join(ROOT, "extras", "js-mirror", "examples", "investigate_repo.js")
FIXTURE_REPO = os.path.join(HERE, "fixtures", "broken_project")


def invoke(engine, cmd, sd, root, *, dep_down=False, crash_apply=False,
           inp=None, answer=None, resolve=None, resolve_value=None):
    if engine == "py":
        argv = [sys.executable, ENGINE_PY, cmd, "--flow", FLOW_PY, "--state-dir", sd]
    else:
        argv = ["node", ENGINE_JS, cmd, "--flow", FLOW_JS, "--state-dir", sd]
    if inp is not None:
        argv += ["--input", inp]
    if answer is not None:
        argv += ["--answer", answer]
    if resolve is not None:
        argv += ["--resolve", resolve]
    if resolve_value is not None:
        argv += ["--resolve-value", resolve_value]
    env = dict(os.environ)
    env["INVESTIGATE_MODE"] = "real"
    env["INVESTIGATE_ROOT"] = root
    if dep_down:
        env["INVESTIGATE_DEP_DOWN"] = "1"
    else:
        env.pop("INVESTIGATE_DEP_DOWN", None)
    if crash_apply:
        env["INVESTIGATE_CRASH_APPLY"] = "1"
    else:
        env.pop("INVESTIGATE_CRASH_APPLY", None)
    p = subprocess.run(argv, capture_output=True, text=True, env=env)
    payload = None
    body = p.stdout.strip()
    if body:
        try:
            payload = json.loads(body.splitlines()[-1])
        except ValueError:
            payload = None
    return p.returncode, payload


def journal(sd):
    jp = os.path.join(sd, "journal.jsonl")
    if not os.path.exists(jp):
        return []
    return [json.loads(x) for x in open(jp) if x.strip()]


def count_started(sd, key):
    return sum(1 for r in journal(sd) if r.get("type") == "step_started" and r.get("key") == key)


def calc_src(root):
    return open(os.path.join(root, "src", "calc.py"), encoding="utf-8").read()


def make_repo():
    base = tempfile.mkdtemp(prefix="inv-integ-")
    repo = os.path.join(base, "repo")
    shutil.copytree(FIXTURE_REPO, repo)
    return base, repo, os.path.join(base, "state")


class Checks:
    def __init__(self, engine):
        self.engine = engine
        self.problems = []

    def __call__(self, cond, msg):
        print("      [%s] %s" % ("ok" if cond else "FAIL", msg))
        if not cond:
            self.problems.append("%s: %s" % (self.engine, msg))


def pick_source(payload):
    """Choose the buggy source file from the focus gate's typed suspect set."""
    enum = (payload or {}).get("pending", {}).get("schema", {}).get("enum", []) or []
    for p in enum:
        if p.endswith("src/calc.py"):
            return p, enum
    return None, enum


def scenario_a(engine, check):
    print("\n  -- Scenario A: broken env -> fix -> resume -> real edit -> real re-run passes")
    base, repo, sd = make_repo()
    try:
        code, _ = invoke(engine, "run", sd, repo, inp='{"goal":"test_add"}', dep_down=True)
        check(code == 1, "A: broken environment fails reproduce (exit 1)")
        check("return a - b" in calc_src(repo), "A: source still buggy after the failed run")

        code, payload = invoke(engine, "run", sd, repo, inp='{"goal":"test_add"}')
        check(code == 10, "A: env fixed, resumed to the focus gate (exit 10)")
        check(count_started(sd, "map") == 1, "A: repo scanned once (memoized across the recovery)")
        check(count_started(sd, "reproduce") == 2, "A: reproduce re-attempted after the fix")
        choice, enum = pick_source(payload)
        check(choice is not None, "A: real grep located src/calc.py among suspects %s" % enum)

        code, payload = invoke(engine, "resume", sd, repo, answer=json.dumps(choice))
        check(code == 10, "A: suspect chosen, proposed, at the approval gate (exit 10)")

        code, payload = invoke(engine, "resume", sd, repo, answer="true")
        check(code == 0, "A: approved -> real edit -> real re-run -> completed (exit 0)")
        res = (payload or {}).get("result", {})
        check(res.get("status") == "fixed", "A: result status is 'fixed'")
        check("OK" in res.get("verify", {}).get("out", ""), "A: the REAL test passed on re-run")
        check("return a + b" in calc_src(repo) and "return a - b" not in calc_src(repo),
              "A: the real source file was actually edited (a - b -> a + b)")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def scenario_crash(engine, check, resolution):
    label = resolution.upper()
    print("\n  -- Scenario %s: crash mid real-edit -> in-doubt -> resolve %s -> verify passes"
          % ("B" if resolution == "completed" else "C", label))
    base, repo, sd = make_repo()
    try:
        code, _ = invoke(engine, "run", sd, repo, inp='{"goal":"test_add"}')
        check(code == 10, "%s: run to focus gate" % label)
        code, payload = invoke(engine, "resume", sd, repo, answer='"src/calc.py"')
        check(code == 10, "%s: at approval gate" % label)

        code, _ = invoke(engine, "resume", sd, repo, answer="true", crash_apply=True)
        check(code == 137, "%s: killed mid real-edit (exit 137)" % label)
        check("return a + b" in calc_src(repo), "%s: the edit had already landed before the crash" % label)

        code, payload = invoke(engine, "run", sd, repo, inp='{"goal":"test_add"}')
        check(code == 11, "%s: re-run escalates to in-doubt (exit 11)" % label)

        rv = '{"patched":"src/calc.py"}' if resolution == "completed" else None
        code, payload = invoke(engine, "resume", sd, repo, resolve=resolution, resolve_value=rv)
        check(code == 0, "%s: resolved '%s' -> completed (exit 0)" % (label, resolution))
        res = (payload or {}).get("result", {})
        check("OK" in res.get("verify", {}).get("out", ""), "%s: the REAL test passed after resolution" % label)
        src = calc_src(repo)
        check(src.count("return a + b") == 1 and "return a - b" not in src,
              "%s: source fixed exactly once (no double edit)" % label)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def run_engine(engine):
    print("\n=== real-repo integration [%s] ===" % engine)
    check = Checks(engine)
    scenario_a(engine, check)
    scenario_crash(engine, check, "completed")
    scenario_crash(engine, check, "retry")
    return check.problems


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["py"], default="py")  # js mirror quarantined
    args = ap.parse_args(argv)
    engines = ["py", "js"] if args.engine == "both" else [args.engine]

    problems = []
    for eng in engines:
        if eng == "js" and not shutil.which("node"):
            print("\n[skip] node not found; cannot run the JS engine integration.")
            continue
        problems += run_engine(eng)

    print("\n=== integration summary ===")
    if problems:
        print("  %d check(s) FAILED:" % len(problems))
        for p in problems:
            print("    - " + p)
        return 1
    print("  All integration checks passed (real repo: red -> fixed -> green; crash mid-edit "
          "resumes consistent; completed + retry).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
