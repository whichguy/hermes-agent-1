#!/usr/bin/env python3
"""Dependency-free test entrypoint for the resumable-script skill.

Runs a JSON-contract self-check, the paths golden matrix, then climbs the escalating
ladder L00..L13 + wf_* + call_* (nested ctx.call, CLI/FileStore) + inv_*, then the
separate run_call_ladder (run_flow/resume_flow/export_portable_state library API,
including nested ctx.call). Exits non-zero if any rung fails. (The retired JS mirror
lives in extras/js-mirror/; the journal format + golden fixtures remain the
language-neutral contract.)

  python3 tests/run.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))


def contract_checks():
    import engine
    assert engine._dumps({"b": 1, "a": 2}) == '{"a":2,"b":1}', "keys must be sorted"
    for bad in (float("nan"), float("inf")):
        try:
            engine._dumps(bad)
            raise AssertionError("NaN/Infinity must be rejected")
        except ValueError:
            pass
    try:
        engine._assert_json_safe({"x": 2 ** 60}, "t")
        raise AssertionError("integer beyond 2^53 must be rejected")
    except ValueError:
        pass
    print("  PASS contract")


def paths_checks():
    # JSONPath resolver + ${...} interpolation golden matrix (tests/paths_cases.json —
    # the language-neutral vector file a second engine would also have to pass).
    import paths_check
    if paths_check.main() != 0:
        raise AssertionError("py paths check failed")


def retired_strings_check():
    # The exit-11 payload options were aligned with the CLI --resolve verbs
    # (completed|retry|abort); the retired strings must not resurface in code or docs.
    import re
    bad = re.compile(r"not_completed|check_and_retry")
    hits = []
    for base in ("scripts", "references", "examples"):
        for root, _dirs, files in os.walk(os.path.join(ROOT, base)):
            for fn in files:
                path = os.path.join(root, fn)
                try:
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                except (UnicodeDecodeError, OSError):
                    continue
                if bad.search(text):
                    hits.append(os.path.relpath(path, ROOT))
    if hits:
        raise AssertionError("retired exit-11 option strings found in: %s" % hits)
    print("  PASS retired-strings")


def tiers_checks():
    # Pin the Codex-review fixes: (a) the TIERS invariants (disjoint + exactly the tier* suites)
    # hold — importing suites already asserts them, but restate for a clear failure; (b) run_tiers
    # ALWAYS leaves a ground-truth artifact even on the bad-name error paths (the regression that
    # let a suite/rung-drift surface downstream as "the agent never ran the suite"); (c) a dispatch
    # nonce is stamped when set and absent otherwise. Offline, stdlib-only; RUN_TIERS_ARTIFACT
    # redirects the write so the real .last_run.json is untouched.
    import json
    import subprocess
    import tempfile
    from suites import SUITES, TIERS
    assert set(TIERS) == {k for k in SUITES if k.startswith("tier")}, "TIERS != tier* suites"
    climb = [r for t in TIERS for r in SUITES[t]]
    assert len(set(climb)) == len(climb), "tiers overlap (a full climb would repeat a rung)"

    rt = os.path.join(HERE, "run_tiers.py")
    for flag, arg, want_exit in [("--only", "nope-suite", 2), ("--through", "nope-tier", 2)]:
        with tempfile.TemporaryDirectory() as td:
            art = os.path.join(td, "art.json")
            env = dict(os.environ, RUN_TIERS_ARTIFACT=art)
            p = subprocess.run([sys.executable, rt, flag, arg], env=env,
                               capture_output=True, text=True)
            assert p.returncode == want_exit, \
                "%s %s: exit %d != %d" % (flag, arg, p.returncode, want_exit)
            assert os.path.exists(art), "%s %s wrote NO artifact (the regression)" % (flag, arg)
            data = json.load(open(art))
            assert data["overall"] == "error" and data["exit"] == want_exit, \
                "%s %s artifact not error-tagged: %r" % (flag, arg, data)

    with tempfile.TemporaryDirectory() as td:
        for label, nonce in [("set", "test-dispatch-nonce"), ("unset", None)]:
            art = os.path.join(td, "%s.json" % label)
            env = dict(os.environ, RUN_TIERS_ARTIFACT=art)
            if nonce is None:
                env.pop("RUN_TIERS_NONCE", None)
            else:
                env["RUN_TIERS_NONCE"] = nonce
            p = subprocess.run([sys.executable, rt, "--only", "tier1-basics"], env=env,
                               capture_output=True, text=True)
            assert p.returncode == 0, "nonce %s run failed: %s" % (label, p.stderr)
            data = json.load(open(art))
            if nonce is None:
                assert "nonce" not in data, "unset nonce was stamped: %r" % data
            else:
                assert data.get("nonce") == nonce, "nonce was not stamped: %r" % data
    print("  PASS tiers (invariants + always-writes-artifact + optional nonce)")

    # ask_run.sh's shell behavior (flag parsing + bounded backup) — offline, stubs docker/rsync.
    check = os.path.join(HERE, "check_ask_run.sh")
    p = subprocess.run(["bash", check], capture_output=True, text=True)
    if p.returncode != 0:
        sys.stdout.write(p.stdout)
        sys.stderr.write(p.stderr)
        raise AssertionError("check_ask_run.sh failed")
    print("  PASS ask_run.sh (--no-sync flag + nonce provenance + clean validation + backup retention)")


def walkthrough_check(evidence=False):
    # The narrated demos are self-checking; run them on both engines so they can't
    # bit-rot. Quiet-unless-fail normally; in evidence mode their full narration (exit
    # code, status, observer trace, journal delta per step) is streamed as the evidence.
    import subprocess
    demos = [
        ("walkthrough",
         "examples/walkthrough.py",
         "crash -> in-doubt -> resolve -> gate -> replay",
         ["--engine", "py"]),
        ("walkthrough-investigate",
         "examples/walkthrough_investigate.py",
         "durable codebase investigation: memoized scan -> fix-and-resume -> "
         "decision gate -> crash mid-edit -> report-only branch",
         ["--engine", "py"]),
        ("walkthrough-nested",
         "examples/walkthrough_nested.py",
         "nested ctx.call + portable state: hoisted suspend -> one-JSON-value run -> "
         "verbatim-key resume (exactly-once) -> fork-risk demo",
         []),   # library-API demo: no engine/state-dir flags
    ]
    for name, rel, blurb, extra in demos:
        argv = [sys.executable, os.path.join(ROOT, rel)] + extra
        if evidence:
            print("\n=== %s (evidence) ===" % name)
            p = subprocess.run(argv)                       # stream the narration
            if p.returncode != 0:
                raise AssertionError("%s demo failed" % name)
        else:
            p = subprocess.run(argv, capture_output=True, text=True)
            if p.returncode != 0:
                sys.stdout.write(p.stdout)
                sys.stderr.write(p.stderr)
                raise AssertionError("%s demo failed" % name)
            print("  PASS %s (%s)" % (name, blurb))


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-integration", action="store_true",
                    help="also run the real-repo integration (shells out; slower)")
    ap.add_argument("--evidence", action="store_true",
                    help="show what the suite did: per-rung engine calls (exit/status/pending) "
                         "and the walkthroughs' full narrated traces")
    args = ap.parse_args(argv)

    import run_ladder
    import run_call_ladder
    contract_checks()
    paths_checks()
    retired_strings_check()
    tiers_checks()
    walkthrough_check(evidence=args.evidence)
    rc = 0
    ladder_args = ["--evidence"] if args.evidence else []
    print("=== ladder ===")
    rc |= run_ladder.main(["--engine", "py"] + ladder_args)
    print("=== call-ladder (run_flow/resume_flow library API) ===")
    rc |= run_call_ladder.main([])   # explicit empty argv — must not inherit OUR flags via sys.argv
    if args.with_integration:
        import run_integration
        print("=== real-repo integration ===")
        rc |= run_integration.main(["--engine", "py"])
    else:
        print("(skipping real-repo integration; pass --with-integration to run it)")
    print("\nALL OK" if rc == 0 else "\nFAILURES (rc=%d)" % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
