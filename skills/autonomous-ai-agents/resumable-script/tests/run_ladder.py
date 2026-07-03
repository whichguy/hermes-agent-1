#!/usr/bin/env python3
"""Escalating ladder driver for the resumable-script engine.

Climbs the rungs L00..L12 in order through the REAL run/resume CLI, checking each
on receipts (exit code + journal shape). Halts at the first failing rung and prints
the level, expected-vs-actual, and a journal dump.

Usage:
  python3 tests/run_ladder.py                 # climb until the first failure
  python3 tests/run_ladder.py -k l05          # only rungs whose name matches
  python3 tests/run_ladder.py --through l08   # stop after this rung
  python3 tests/run_ladder.py --suite smoke   # run a named suite (see tests/suites.py)
  python3 tests/run_ladder.py --list-suites   # list suites + validate their rung names
"""
import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LADDER = os.path.join(HERE, "ladder")
PY_ENGINE = os.path.join(ROOT, "scripts", "engine.py")
JS_ENGINE = os.path.join(ROOT, "extras", "js-mirror", "scripts", "engine.js")  # quarantined mirror

EXIT = {
    "ok": 0, "failed": 1, "usage": 2, "skew": 3,
    "suspended": 10, "in_doubt": 11, "no_autoanswer": 12, "busy": 13,
}


class LadderError(AssertionError):
    pass


# Evidence mode: when `_EV` is a list, every CLI call and every verified invariant is
# recorded so the driver can SHOW what a rung actually did (the engine's real receipts),
# not just PASS/FAIL. Off by default (`_EV is None`) — normal runs stay quiet and fast.
_EV = None
_EV_CHECKS = 0


def _ev_env(env):
    """Compact a rung's env overrides for the evidence line (basename long paths)."""
    shown = {}
    for k, v in (env or {}).items():
        if k == "INVESTIGATE_MODE":
            continue
        shown["fixture" if k == "INVESTIGATE_FIXTURE" else k] = (
            os.path.basename(v) if k == "INVESTIGATE_FIXTURE" else v)
    return ("env{%s}" % ", ".join("%s=%s" % kv for kv in sorted(shown.items()))) if shown else ""


_EXIT_NAME = {v: k for k, v in EXIT.items()}


def _ev_record(cmd, stem, opts, run):
    status, pend = "-", None
    if run.payload:
        status = run.payload.get("status", "-")
        pend = (run.payload.get("pending") or {}).get("key")
    args = ["%s=%s" % (k, opts[k]) for k in ("input", "answer", "resolve", "resolve_value", "key")
            if k in opts]
    e = _ev_env(opts.get("env"))
    if e:
        args.append(e)
    argstr = ("  " + "  ".join(args)) if args else ""
    tail = ("  status=%s" % status) if status != "-" else ""
    tail += ("  pending=%s" % pend) if pend else ""
    name = _EXIT_NAME.get(run.code) or ("crash/killed" if run.code >= 128 else "?")
    _EV.append("       $ %s %s%s  ->  exit %d (%s)%s"
               % (cmd, stem, argstr, run.code, name, tail))


class Run:
    """One CLI invocation result."""
    def __init__(self, code, payload, raw):
        self.code = code
        self.payload = payload
        self.raw = raw


class Harness:
    def __init__(self, engine, state_dir):
        self.engine = engine          # "py" or "js"
        self.state_dir = state_dir

    def _flow_path(self, stem):
        ext = "py" if self.engine == "py" else "js"
        return os.path.join(LADDER, "%s.%s" % (stem, ext))

    def invoke(self, cmd, stem, **opts):
        flow = self._flow_path(stem)
        if self.engine == "py":
            argv = [sys.executable, PY_ENGINE, cmd, "--flow", flow]
        else:
            argv = ["node", JS_ENGINE, cmd, "--flow", flow]
        argv += ["--state-dir", self.state_dir]
        if "input" in opts:
            argv += ["--input", opts["input"]]
        if "answer" in opts:
            argv += ["--answer", opts["answer"]]
        if opts.get("auto"):
            argv += ["--auto"]
        if opts.get("no_strict"):
            argv += ["--no-strict"]
        if opts.get("accept_flow_change"):
            argv += ["--accept-flow-change"]
        if "key" in opts:
            argv += ["--key", opts["key"]]
        if "resolve" in opts:
            argv += ["--resolve", opts["resolve"]]
        if "resolve_key" in opts:
            argv += ["--resolve-key", opts["resolve_key"]]
        if "resolve_value" in opts:
            argv += ["--resolve-value", opts["resolve_value"]]
        if "output_file" in opts:
            argv += ["--output-file", opts["output_file"]]
        env = dict(os.environ)
        env.update(opts.get("env", {}))
        proc = subprocess.run(argv, capture_output=True, text=True, env=env)
        raw = proc.stdout.strip()
        payload = None
        if raw:
            try:
                payload = json.loads(raw.splitlines()[-1])
            except ValueError:
                payload = None
        if proc.returncode not in EXIT.values():
            sys.stderr.write(proc.stderr)
        run = Run(proc.returncode, payload, proc.stderr.strip())
        if _EV is not None:
            _ev_record(cmd, stem, opts, run)
        return run

    def journal(self):
        path = os.path.join(self.state_dir, "journal.jsonl")
        if not os.path.exists(path):
            return []
        out = []
        with open(path, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    out.append(json.loads(ln))
        return out

    def count_started(self, key):
        return sum(1 for r in self.journal()
                   if r.get("type") == "step_started" and r.get("key") == key)

    def count_type(self, type_, key=None):
        return sum(1 for r in self.journal()
                   if r.get("type") == type_ and (key is None or r.get("key") == key))

    def records_of(self, type_):
        return [r for r in self.journal() if r.get("type") == type_]

    def has_started(self, key):
        return self.count_started(key) > 0

    def seed_journal(self, records):
        os.makedirs(self.state_dir, exist_ok=True)
        path = os.path.join(self.state_dir, "journal.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for i, r in enumerate(records):
                r = dict(r)
                r.setdefault("v", 1)
                r.setdefault("seq", i)
                r.setdefault("ts", "2026-01-01T00:00:00Z")
                f.write(json.dumps(r, sort_keys=True) + "\n")


@contextlib.contextmanager
def fresh(engine, tag):
    d = tempfile.mkdtemp(prefix="ladder-%s-" % tag)
    try:
        yield Harness(engine, d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- assertions
def expect(cond, msg):
    if not cond:
        raise LadderError(msg)
    global _EV_CHECKS
    if _EV is not None:
        _EV_CHECKS += 1


def expect_code(run, name, where):
    expect(run.code == EXIT[name],
           "%s: expected exit %d (%s), got %d. stderr=%s payload=%s"
           % (where, EXIT[name], name, run.code, run.raw, run.payload))


# --------------------------------------------------------------------------- rungs
def l00(h):
    r = h.invoke("run", "l00_linear", input="null")
    expect_code(r, "ok", "L00 run")
    expect(r.payload["status"] == "completed" and r.payload["result"] == {"sum": 3},
           "L00 result wrong: %s" % r.payload)
    for k in ("a", "b", "c"):
        expect(h.count_started(k) == 1, "L00 step %s started %d times" % (k, h.count_started(k)))


def l01(h):
    r1 = h.invoke("run", "l01_memo", input="null")
    expect_code(r1, "ok", "L01 first run")
    r2 = h.invoke("run", "l01_memo", input="null")
    expect_code(r2, "ok", "L01 second run")
    expect(r1.payload["result"] == r2.payload["result"] == {"x": 42}, "L01 result drift")
    expect(h.count_started("compute") == 1,
           "L01 memoization broken: compute started %d times" % h.count_started("compute"))


def l02(h):
    r = h.invoke("run", "l02_suspend", input="null")
    expect_code(r, "suspended", "L02 run")
    expect(r.payload["pending"]["key"] == "confirm", "L02 wrong pending key")
    r2 = h.invoke("resume", "l02_suspend", answer="true")
    expect_code(r2, "ok", "L02 resume")
    expect(r2.payload["result"] == {"go": True}, "L02 result wrong: %s" % r2.payload)


def l03(h):
    r = h.invoke("run", "l03_multi_suspend", input="null")
    expect_code(r, "suspended", "L03 run")
    r2 = h.invoke("resume", "l03_multi_suspend", answer="true")
    expect_code(r2, "ok", "L03 resume")
    expect(r2.payload["result"] == {"c": 13}, "L03 result wrong: %s" % r2.payload)
    for k in ("a", "b", "c"):
        expect(h.count_started(k) == 1, "L03 step %s re-fired: %d" % (k, h.count_started(k)))


def l04(h):
    r = h.invoke("run", "l04_chained", input="null")
    expect_code(r, "suspended", "L04 run")
    expect(r.payload["pending"]["key"] == "q1", "L04 expected q1 first")
    r2 = h.invoke("resume", "l04_chained", answer="1")
    expect_code(r2, "suspended", "L04 resume-1")
    expect(r2.payload["pending"]["key"] == "q2", "L04 expected q2 second")
    r3 = h.invoke("resume", "l04_chained", answer="2")
    expect_code(r3, "ok", "L04 resume-2")
    expect(r3.payload["result"] == {"x": 1, "y": 2}, "L04 result wrong: %s" % r3.payload)


def l05(h):
    r = h.invoke("run", "l05_branch", input="null")
    expect_code(r, "suspended", "L05 run")
    r2 = h.invoke("resume", "l05_branch", answer='"b"')
    expect_code(r2, "ok", "L05 resume")
    expect(r2.payload["result"] == {"v": "B"}, "L05 result wrong: %s" % r2.payload)
    expect(h.has_started("branch-b") and not h.has_started("branch-a"),
           "L05 wrong branch executed")


def l06(h):
    # loop with data-derived keys, suspended mid-loop
    r = h.invoke("run", "l06_loop", input="null")
    expect_code(r, "suspended", "L06 run")
    expect(r.payload["pending"]["key"] == "pause", "L06 expected pause gate")
    r2 = h.invoke("resume", "l06_loop", answer="true")
    expect_code(r2, "ok", "L06 resume")
    expect(r2.payload["result"] == {"out": ["X", "Y", "Z"]}, "L06 result wrong: %s" % r2.payload)
    for k in ("items", "item:x", "item:y", "item:z"):
        expect(h.count_started(k) == 1, "L06 %s re-fired: %d" % (k, h.count_started(k)))
    # duplicate key in one pass -> KeyCollision (exit 2). Fresh state dir.
    with fresh(h.engine, "l06c") as hc:
        c = hc.invoke("run", "l06c_collision", input="null")
        expect_code(c, "usage", "L06 collision")


def l07(h):
    r = h.invoke("run", "l07_retries", input="null")
    expect_code(r, "ok", "L07 run")
    expect(r.payload["result"] == {"v": "ok"}, "L07 result wrong: %s" % r.payload)
    expect(h.count_started("flaky") == 3, "L07 expected 3 attempts, got %d" % h.count_started("flaky"))
    fails = [x for x in h.records_of("step_failed") if x["key"] == "flaky"]
    expect(len(fails) == 2, "L07 expected 2 failed attempts, got %d" % len(fails))
    # retriable must be True while attempts remain (retries=3), then the step succeeds on
    # attempt 3 so there is never a terminal (retriable=False) step_failed to observe here.
    expect(all(f["error"]["retriable"] is True for f in fails),
           "L07 retriable flag wrong: %s" % [f["error"] for f in fails])


def l08(h):
    # idempotent in-doubt: dangling step re-runs safely
    h.seed_journal([
        {"type": "run_started", "run_id": "R", "flow_id": "l08i", "flow_version": 1,
         "engine": "py", "input": None},
        {"type": "step_started", "key": "act", "attempt": 1, "idempotency_key": "R:act"},
    ])
    r = h.invoke("run", "l08i_idem", input="null")
    expect_code(r, "ok", "L08 idempotent re-run")
    expect(r.payload["result"] == {"v": "done"}, "L08 idem result wrong: %s" % r.payload)
    expect(h.count_started("act") == 2, "L08 idem expected re-run (2 starts), got %d" % h.count_started("act"))
    # non-idempotent in-doubt: escalates rather than blind re-run
    with fresh(h.engine, "l08n") as h2:
        h2.seed_journal([
            {"type": "run_started", "run_id": "R", "flow_id": "l08n", "flow_version": 1,
             "engine": "py", "input": None},
            {"type": "step_started", "key": "act", "attempt": 1, "idempotency_key": "R:act"},
        ])
        rn = h2.invoke("run", "l08n_nonidem", input="null")
        expect_code(rn, "in_doubt", "L08 non-idempotent escalation")
        expect(h2.count_started("act") == 1, "L08 non-idem must NOT re-run: %d" % h2.count_started("act"))


def l10(h):
    # interpreter: free-form reply -> schema answer, journaled as interpreted_by=llm
    r = h.invoke("run", "l10_interp", input="null")
    expect_code(r, "suspended", "L10 interp run")
    r2 = h.invoke("resume", "l10_interp", answer="yeah go ahead")
    expect_code(r2, "ok", "L10 interp resume")
    expect(r2.payload["result"] == {"x": "exposed", "go": True}, "L10 interp result: %s" % r2.payload)
    ans = [r for r in h.records_of("ask_answered") if r["key"] == "confirm"]
    expect(ans and ans[-1]["interpreted_by"] == "llm" and ans[-1]["answer"] is True,
           "L10 interpreter did not normalize via llm: %s" % ans)
    # adjudicator: failed step resolved by the adjudicator hook
    with fresh(h.engine, "l10a") as ha:
        ra = ha.invoke("run", "l10_adjudge", input="null")
        expect_code(ra, "ok", "L10 adjudicate run")
        expect(ra.payload["result"] == {"v": "skipped-by-adjudicator"},
               "L10 adjudicate result: %s" % ra.payload)
        expect(ha.count_type("step_failed", "risky") == 1, "L10 expected one failed attempt")
        adj = [r for r in ha.records_of("ask_answered") if r["key"].startswith("__adjudicate")]
        expect(adj and adj[-1]["interpreted_by"] == "llm", "L10 missing adjudication record")


def l11(h):
    # (a) torn-tail journal line is dropped on read; the step re-runs cleanly
    with fresh(h.engine, "l11torn") as ht:
        os.makedirs(ht.state_dir, exist_ok=True)
        jp = os.path.join(ht.state_dir, "journal.jsonl")
        with open(jp, "w", encoding="utf-8") as f:
            f.write(json.dumps({"v": 1, "seq": 0, "ts": "2026-01-01T00:00:00Z",
                                "type": "run_started", "run_id": "R", "flow_id": "l11torn",
                                "flow_version": 1,
                                "engine": ht.engine, "input": None}, sort_keys=True) + "\n")
            f.write('{"v":1,"seq":1,"type":"step_comple')   # torn: no newline, partial
        r = ht.invoke("run", "l11_torn", input="null")
        expect_code(r, "ok", "L11 torn-tail run")
        expect(r.payload["result"] == {"v": "v1"}, "L11 torn result: %s" % r.payload)

    # (b) large result spills to a blob with result_ref + sha
    with fresh(h.engine, "l11blob") as hb:
        r = hb.invoke("run", "l11_blob", input="null")
        expect_code(r, "ok", "L11 blob run")
        expect(r.payload["result"] == {"len": 70000}, "L11 blob result: %s" % r.payload)
        comp = [x for x in hb.records_of("step_completed") if x["key"] == "big"]
        expect(comp and "result_ref" in comp[0] and "result_sha256" in comp[0],
               "L11 blob: result not spilled to a ref: %s" % comp)
        expect(os.path.isdir(os.path.join(hb.state_dir, "blobs")), "L11 blob: blobs/ missing")
        # memoized from the blob on re-run
        r2 = hb.invoke("run", "l11_blob", input="null")
        expect_code(r2, "ok", "L11 blob re-run")
        expect(hb.count_started("big") == 1, "L11 blob not memoized from ref")

    # (c) duplicate key -> exit 2
    with fresh(h.engine, "l11coll") as hc:
        expect_code(hc.invoke("run", "l06c_collision", input="null"), "usage", "L11 collision")

    # (d) un-stepped nondeterminism (env flip) -> strict-replay skew -> exit 3
    with fresh(h.engine, "l11flip") as hf:
        r = hf.invoke("run", "l11_flip", input="null", env={"FLIP": "a"})
        expect_code(r, "suspended", "L11 flip run")
        r2 = hf.invoke("resume", "l11_flip", answer="true", env={"FLIP": "b"})
        expect_code(r2, "skew", "L11 flip divergence")

    # (e) lock held by another writer -> exit 13 with a machine-readable {"status":"busy"} payload
    with fresh(h.engine, "l11lock") as hl:
        os.makedirs(hl.state_dir, exist_ok=True)
        lock_path = os.path.join(hl.state_dir, "lock")
        if h.engine == "py":
            import fcntl
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                r = hl.invoke("run", "l00_linear", input="null")
                expect_code(r, "busy", "L11 lock busy")
                expect(r.payload == {"status": "busy"}, "L11 busy payload missing: %s" % r.payload)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
        else:
            # Node uses an O_EXCL lockfile: pre-create it holding a LIVE pid so the
            # stale-takeover path doesn't steal it.
            with open(lock_path, "w") as f:
                f.write(str(os.getpid()))
            r = hl.invoke("run", "l00_linear", input="null")
            expect_code(r, "busy", "L11 lock busy")
            expect(r.payload == {"status": "busy"}, "L11 busy payload missing: %s" % r.payload)
            os.unlink(lock_path)

    # (f) [removed] "foreign/unreadable lock holder, dead-pid stale takeover" was a JS-only
    # scenario (JS's O_EXCL lockfile has no kernel-level auto-release, so it had to read the
    # holder pid itself). Py's flock is kernel-authoritative: the OS releases it the instant
    # the holding process dies, with no pid-liveness logic to test. Since the JS mirror is
    # quarantined (`--engine` now only accepts "py"), this sub-case was permanently
    # unreachable dead code (`if h.engine == "js":` never true) — removed rather than kept
    # as a false-positive "tested" scenario. No py-equivalent behavior exists to test here.


VOLATILE = {"ts", "seq", "run_id", "flow_hash", "idempotency_key", "engine", "engine_version"}
REQUIRED = {"v", "seq", "ts", "type"}


def _normalize(records):
    return [{k: v for k, v in r.items() if k not in VOLATILE} for r in records]


def _run_mirror(engine):
    """Run the l09 mirror flow to completion in one engine; return its journal."""
    with fresh(engine, "l09-%s" % engine) as h:
        r = h.invoke("run", "l09_mirror", input="null")
        expect_code(r, "suspended", "L09 %s run" % engine)
        r2 = h.invoke("resume", "l09_mirror", answer="true")
        expect_code(r2, "ok", "L09 %s resume" % engine)
        expect(r2.payload["result"] == {"a": 1, "out": ["P", "Q"], "ok": True},
               "L09 %s result: %s" % (engine, r2.payload))
        journal = h.journal()
        for rec in journal:
            expect(REQUIRED.issubset(rec.keys()), "L09 %s record missing required fields: %s" % (engine, rec))
        return journal


def l09(h):
    # FORMAT pin: the mirror flow's normalized journal must match the golden fixture —
    # the journal format is the language-neutral contract a future second engine must
    # reproduce (references/journal-format.md §Portability; the retired JS mirror lives
    # in extras/js-mirror/).
    journal = _run_mirror("py")
    norm = _normalize(journal)
    fixture_path = os.path.join(ROOT, "assets", "journal-fixtures", "l09-mirror.normalized.json")
    with open(fixture_path, encoding="utf-8") as f:
        golden = json.load(f)
    expect(norm == golden,
           "L09 journal diverges from the golden format fixture:\n  got=%s\n  want=%s"
           % (json.dumps(norm, sort_keys=True), json.dumps(golden, sort_keys=True)))


def l12(h):
    # kitchen-sink: loop + branch + 2 suspends + a transient retry, in one flow.
    r = h.invoke("run", "l12_sink", input="null")
    expect_code(r, "suspended", "L12 run")
    expect(r.payload["pending"]["key"] == "plan", "L12 expected plan gate first")
    r2 = h.invoke("resume", "l12_sink", answer='"pro"')
    expect_code(r2, "suspended", "L12 resume-1")
    expect(r2.payload["pending"]["key"] == "confirm", "L12 expected confirm gate second")
    r3 = h.invoke("resume", "l12_sink", answer="true")
    expect_code(r3, "ok", "L12 resume-2")
    expect(r3.payload["result"] == {"region": "us", "plan": "pro",
                                    "processed": ["a!", "b!", "c!"], "flaky": "ok",
                                    "final": "committed"},
           "L12 result: %s" % r3.payload)
    for k in ("region", "items", "proc:a", "proc:b", "proc:c", "commit"):
        expect(h.count_started(k) == 1, "L12 %s re-fired: %d" % (k, h.count_started(k)))
    expect(h.count_started("flaky") == 3, "L12 flaky attempts: %d" % h.count_started("flaky"))
    expect(not h.has_started("rollback"), "L12 wrong branch (rollback) executed")


def l13_onfail(h):
    # on_fail policy hook: retry-once-then-catch memoizes an __error__ sentinel; the branch
    # taken is replay-stable; "raise" mode keeps today's exit-1 (with step provenance).
    r = h.invoke("run", "l13_onfail", input="null")
    expect_code(r, "ok", "L13 catch completes")
    expect(r.payload["result"] == {"attempts": 2, "caught": True, "name": "RuntimeError"},
           "L13 caught result wrong: %s" % r.payload)
    expect(h.count_started("risky") == 2, "L13 expected 2 attempts, got %d" % h.count_started("risky"))
    expect(h.count_type("step_failed", "risky") == 2,
           "L13 expected 2 step_failed, got %d" % h.count_type("step_failed", "risky"))
    completes = [x for x in h.records_of("step_completed") if x.get("key") == "risky"]
    expect(len(completes) == 1 and completes[0].get("result") ==
           {"__error__": {"attempts": 2, "message": "kaboom", "name": "RuntimeError"}},
           "L13 synthesized sentinel record wrong: %s" % completes)
    # re-run = pure replay: same branch, zero new starts
    r2 = h.invoke("run", "l13_onfail", input="null")
    expect_code(r2, "ok", "L13 re-run")
    expect(r2.payload["result"] == r.payload["result"], "L13 result drift on replay")
    expect(h.count_started("risky") == 2, "L13 replay re-ran the caught step")
    # raise mode -> exit 1 with step/attempts provenance
    with fresh(h.engine, "l13r") as h2:
        rr = h2.invoke("run", "l13_onfail", input="null", env={"ONFAIL_MODE": "raise"})
        expect_code(rr, "failed", "L13 raise mode")
        err = rr.payload["error"]
        expect(err.get("step") == "risky" and err.get("attempts") == 1,
               "L13 provenance wrong: %s" % err)


def lfailmeta(h):
    # exit-1 provenance: error.step/attempts identify the failing step and accumulate across
    # invocations (the driver's deterministic-failure discriminator); glue failures stay bare.
    r = h.invoke("run", "e2_recover", input="null", env={"API_DOWN": "1"})
    expect_code(r, "failed", "LFMETA first failure")
    err = r.payload["error"]
    expect(err.get("step") == "call-api" and err.get("attempts") == 1,
           "LFMETA provenance wrong: %s" % err)
    # retriable=False on the terminal attempt (call-api has no retries -> attempt(1) > retries(0))
    fail_rec = [x for x in h.records_of("step_failed") if x["key"] == "call-api"][0]
    expect(fail_rec["error"]["retriable"] is False,
           "LFMETA terminal step_failed must be retriable=False: %s" % fail_rec["error"])
    r2 = h.invoke("run", "e2_recover", input="null", env={"API_DOWN": "1"})
    err2 = r2.payload["error"]
    expect(err2.get("attempts") == 2 and err2["name"] == err["name"]
           and err2["message"] == err["message"],
           "LFMETA cross-invocation attempts wrong: %s vs %s" % (err, err2))
    st = json.load(open(os.path.join(h.state_dir, "state.json"), encoding="utf-8"))
    expect(st["error"] == err2, "LFMETA state.json error must mirror stdout: %s" % st["error"])
    # glue failure keeps the bare {name,message} shape
    with fresh(h.engine, "glue") as h2:
        rg = h2.invoke("run", "l_glueerr", input="null")
        expect_code(rg, "failed", "LFMETA glue failure")
        eg = rg.payload["error"]
        expect(eg["name"] == "ValueError" and "step" not in eg and "attempts" not in eg,
               "LFMETA glue error must stay bare: %s" % eg)


def lprop(h):
    # Replay-determinism property: re-running a completed flow re-executes NO step
    # and yields an identical result, for every completing flow.
    cases = ["l00_linear", "l01_memo", "l07_retries", "l11_blob"]
    for stem in cases:
        with fresh(h.engine, "prop-%s" % stem) as hp:
            r0 = hp.invoke("run", stem, input="null")
            expect_code(r0, "ok", "Lprop %s first run" % stem)
            base_result = r0.payload["result"]
            base_starts = len(hp.records_of("step_started"))
            for k in range(3):
                rk = hp.invoke("run", stem, input="null")
                expect_code(rk, "ok", "Lprop %s re-run %d" % (stem, k))
                expect(rk.payload["result"] == base_result,
                       "Lprop %s result drifted on re-run: %s != %s" % (stem, rk.payload["result"], base_result))
                now = len(hp.records_of("step_started"))
                expect(now == base_starts,
                       "Lprop %s re-executed a completed step (%d -> %d starts)" % (stem, base_starts, now))


def lvalues(h):
    # Portability of tricky-but-safe values (unicode, 2^53-1, nesting) through the
    # journal round-trip — the JSON-contract boundary a second engine must also honour.
    with fresh("py", "values-py") as hh:
        r = hh.invoke("run", "l_values", input="null")
        expect_code(r, "ok", "Lvalues run")
        pres = r.payload["result"]
    expect(pres["big"] == 9007199254740991 and pres["unicode"] == "héllo — 世界 🚀",
           "Lvalues boundary value corrupted: %s" % pres)


def lauto(h):
    # Headless --auto: schema.default, interpreter, and the no-answer failure.
    with fresh(h.engine, "auto-def") as ha:
        r = ha.invoke("run", "l_auto_default", input="null", auto=True)
        expect_code(r, "ok", "Lauto default run")
        expect(r.payload["result"] == {"go": True}, "Lauto default result: %s" % r.payload)
        ans = ha.records_of("ask_answered")
        expect(ans and ans[-1]["interpreted_by"] == "default", "Lauto: expected default-answered gate")
    with fresh(h.engine, "auto-interp") as hi:
        r = hi.invoke("run", "l10_interp", input="null", auto=True)
        expect_code(r, "ok", "Lauto interpreter run")
        ans = hi.records_of("ask_answered")
        expect(ans and ans[-1]["interpreted_by"] == "llm" and ans[-1]["answer"] is False,
               "Lauto interpreter answer wrong: %s" % ans)
        expect(r.payload["result"] == {"x": "private", "go": False},
               "Lauto interpreter result: %s" % r.payload)
    with fresh(h.engine, "auto-none") as hn:
        r = hn.invoke("run", "l02_suspend", input="null", auto=True)
        expect_code(r, "no_autoanswer", "Lauto: no default + no interpreter must exit 12")
        p = r.payload or {}
        expect(p.get("status") == "needs_answer" and (p.get("pending") or {}).get("key"),
               "Lauto: exit 12 must carry a needs_answer payload, got %s" % r.payload)
    with fresh(h.engine, "auto-badd") as hb:
        # An invalid schema `default` must be REJECTED (not memoized forever): exit 12,
        # needs_answer payload with the validation error, no ask_answered journaled.
        r = hb.invoke("run", "l_auto_baddefault", input="null", auto=True)
        expect_code(r, "no_autoanswer", "Lauto: invalid default must exit 12")
        p = r.payload or {}
        expect(p.get("status") == "needs_answer" and "auto-answer rejected" in (p.get("error") or ""),
               "Lauto: invalid-default payload unclear: %s" % r.payload)
        expect(hb.count_type("ask_answered") == 0, "Lauto: invalid default was journaled as answered")
        r2 = hb.invoke("resume", "l_auto_baddefault", answer='"yes"')
        expect_code(r2, "ok", "Lauto: gate stays open for a corrected human answer")


def lidem(h):
    # Idempotency-key dedupe: a keyed step re-runs across a crash-window in-doubt,
    # but the downstream dedupes on run_id:key so the effect applies at most once.
    with fresh(h.engine, "idem") as hh:
        os.makedirs(hh.state_dir, exist_ok=True)
        ledger = os.path.join(hh.state_dir, "ledger.txt")
        with open(ledger, "w", encoding="utf-8") as f:
            f.write("R:charge\n")  # downstream already applied the effect pre-crash
        hh.seed_journal([
            {"type": "run_started", "run_id": "R", "flow_id": "lidem", "flow_version": 1,
             "engine": hh.engine, "input": None},
            {"type": "step_started", "key": "charge", "attempt": 1, "idempotency_key": "R:charge"},
        ])
        r = hh.invoke("run", "l_idemdedupe", input="null", env={"LEDGER": ledger})
        expect_code(r, "ok", "Lidem run")
        expect(r.payload["result"] == {"applied_now": False},
               "Lidem: re-run should be deduped by the key, got %s" % r.payload)
        expect(hh.count_started("charge") == 2, "Lidem: step should have re-run (2 starts)")
        with open(ledger, encoding="utf-8") as f:
            occ = f.read().split().count("R:charge")
        expect(occ == 1, "Lidem: downstream double-applied (ledger has %d occurrences)" % occ)


def lhelpers(h):
    # now()/random()/uuid() are memoized; wait() is the gate. After a suspend at the
    # gate, the helpers replay as memo hits (each started exactly once).
    r = h.invoke("run", "l_helpers", input="null")
    expect_code(r, "suspended", "Lhelpers run")
    expect(r.payload["pending"]["key"] == "gate", "Lhelpers expected wait gate")
    r2 = h.invoke("resume", "l_helpers", answer="true")
    expect_code(r2, "ok", "Lhelpers resume")
    res = r2.payload["result"]
    expect(res["has_time"] and res["has_rand"] and res["has_uuid"] and res["go"] is True,
           "Lhelpers result: %s" % res)
    for k in ("__now:0", "__rand:0", "__uuid:0"):
        expect(h.count_started(k) == 1, "Lhelpers %s not memoized across suspend: %d" % (k, h.count_started(k)))


def loutfile(h):
    # --output-file redirects the terminal JSON payload to a designated file instead of stdout
    # (a headless driver polls ONE durable location instead of capturing a subprocess's stdout);
    # HERMES_OUTPUT_FILE is the equivalent env-var fallback. Neither flag/env given -> unchanged
    # default (payload on stdout), which every OTHER rung in this ladder already exercises.
    with fresh(h.engine, "loutfile") as hh:
        out1 = os.path.join(hh.state_dir, "out1.json")
        r = hh.invoke("run", "l02_suspend", input="null", output_file=out1)
        expect_code(r, "suspended", "Loutfile run")
        expect(r.payload is None, "Loutfile: stdout must be silent when --output-file is set, "
                                  "got payload on stdout: %s" % r.payload)
        filed = json.load(open(out1, encoding="utf-8"))
        expect(filed["status"] == "suspended" and filed["pending"]["key"] == "confirm",
               "Loutfile file payload (suspend) wrong: %s" % filed)

        out2 = os.path.join(hh.state_dir, "out2.json")
        r2 = hh.invoke("resume", "l02_suspend", answer="true", env={"HERMES_OUTPUT_FILE": out2})
        expect_code(r2, "ok", "Loutfile resume via HERMES_OUTPUT_FILE")
        expect(r2.payload is None, "Loutfile: HERMES_OUTPUT_FILE must also silence stdout: %s"
                                   % r2.payload)
        filed2 = json.load(open(out2, encoding="utf-8"))
        expect(filed2["status"] == "completed" and filed2["result"] == {"go": True},
               "Loutfile file payload (resume) wrong: %s" % filed2)


def loutfile_badpath(h):
    # A bad --output-file target (unwritable/nonexistent directory) must never crash the run
    # or lose the terminal payload: _emit falls back to stdout, noting why on stderr.
    with fresh(h.engine, "loutfile-bad") as hh:
        bad = os.path.join(hh.state_dir, "does", "not", "exist", "out.json")
        r = hh.invoke("run", "l00_linear", input="null", output_file=bad)
        expect_code(r, "ok", "Loutfile-bad run")
        expect(r.payload == {"status": "completed", "result": {"sum": 3}},
               "Loutfile-bad: payload must still reach stdout on fallback: %s" % r.payload)
        expect("output-file" in r.raw, "Loutfile-bad: expected a stderr note about the "
                                       "failed write, got: %s" % r.raw)


# ── End-to-end scenario flows (realistic user stories) ───────────────────────
def e1(h):
    # Intervention -> the user fixes a precondition in the system -> resume succeeds.
    with fresh(h.engine, "e1") as hh:
        os.makedirs(hh.state_dir, exist_ok=True)
        cfg = os.path.join(hh.state_dir, "config.txt")
        r = hh.invoke("run", "e1_fixconfig", input="null", env={"CFG": cfg})
        expect_code(r, "suspended", "E1 run (await fix)")
        with open(cfg, "w", encoding="utf-8") as f:  # user fixes the system out-of-band
            f.write("region=eu")
        r2 = hh.invoke("resume", "e1_fixconfig", answer="true", env={"CFG": cfg})
        expect_code(r2, "ok", "E1 resume after fix")
        expect(r2.payload["result"] == {"cfg": "region=eu", "ack": True}, "E1 result: %s" % r2.payload)


def e2(h):
    # Step fails on a down dependency; fix it; re-run re-attempts ONLY the failed step.
    with fresh(h.engine, "e2") as hh:
        r = hh.invoke("run", "e2_recover", input="null", env={"API_DOWN": "1"})
        expect_code(r, "failed", "E2 run (dependency down)")
        r2 = hh.invoke("run", "e2_recover", input="null")  # dependency restored
        expect_code(r2, "ok", "E2 re-run after fix")
        expect(r2.payload["result"] == {"setup": "ready", "api": {"ok": True}}, "E2 result: %s" % r2.payload)
        expect(hh.count_started("setup") == 1, "E2 setup must be memoized (1 start)")
        expect(hh.count_started("call-api") == 2, "E2 call-api must re-attempt (2 starts)")


def e3(h):
    # Real crash-window: side effect lands, process hard-exits before journaling
    # completion; restart re-runs the step but the key dedupes -> applied once.
    with fresh(h.engine, "e3") as hh:
        os.makedirs(hh.state_dir, exist_ok=True)
        ledger = os.path.join(hh.state_dir, "ledger.txt")
        r = hh.invoke("run", "e3_crash", input="null", env={"LEDGER": ledger, "CRASH": "1"})
        expect(r.code == 137, "E3 expected a hard crash (137), got %d (stderr=%s)" % (r.code, r.raw))
        r2 = hh.invoke("run", "e3_crash", input="null", env={"LEDGER": ledger})
        expect_code(r2, "ok", "E3 recovery run")
        expect(r2.payload["result"] == {"charged": True}, "E3 result: %s" % r2.payload)
        expect(hh.count_started("charge") == 2, "E3 charge should re-run after crash (2 starts)")
        with open(ledger, encoding="utf-8") as f:
            lines = [x for x in f.read().split("\n") if x]
        expect(len(lines) == 1, "E3 double-applied across the crash: ledger=%s" % lines)


def e4(h):
    # adjudicator ABORT -> terminal failure with the abort message (distinct from the
    # no-adjudicator fallthrough, which e4 used to be unable to tell apart).
    with fresh(h.engine, "e4a") as ha:
        r = ha.invoke("run", "l10_adjudge", input="null", env={"ADJ_MODE": "abort"})
        expect_code(r, "failed", "E4 adjudicator abort -> exit 1")
        expect(r.payload["error"]["message"] == "adjudicator aborted at risky",
               "E4 abort branch not taken: %s" % r.payload)
        adj = [x for x in ha.records_of("ask_answered") if x["key"] == "__adjudicate:risky"]
        expect(adj and adj[-1]["answer"]["action"] == "abort", "E4 missing abort decision record")
    # an UNKNOWN decision must propagate the ORIGINAL failure, not silently skip.
    with fresh(h.engine, "e4u") as hu:
        r = hu.invoke("run", "l10_adjudge", input="null", env={"ADJ_MODE": "unknown"})
        expect_code(r, "failed", "E4 unknown decision -> exit 1")
        expect("kaboom" in r.payload["error"]["message"],
               "E4 unknown decision should surface the original error: %s" % r.payload)


def e5(h):
    # Two independent runs (distinct state dirs); input flows through and survives resume.
    with fresh(h.engine, "e5a") as ha, fresh(h.engine, "e5b") as hb:
        expect_code(ha.invoke("run", "e_echo", input='{"who":"alice"}'), "suspended", "E5 run A")
        expect_code(hb.invoke("run", "e_echo", input='{"who":"bob"}'), "suspended", "E5 run B")
        ra = ha.invoke("resume", "e_echo", answer="true")
        rb = hb.invoke("resume", "e_echo", answer="true")
        expect_code(ra, "ok", "E5 resume A")
        expect_code(rb, "ok", "E5 resume B")
        expect(ra.payload["result"] == {"input": {"who": "alice"}, "go": True}, "E5 A: %s" % ra.payload)
        expect(rb.payload["result"] == {"input": {"who": "bob"}, "go": True}, "E5 B: %s" % rb.payload)


def e6(h):
    # Enforced flow_hash: a journal written by different source REFUSES to resume (exit 3),
    # even though the key sequence matches — editing a step BODY is invisible to the strict
    # guard. --accept-flow-change proceeds and journals a flow_changed audit record.
    def seed(hh):
        hh.seed_journal([
            {"type": "run_started", "run_id": "R", "flow_id": "l00", "flow_version": 1,
             "flow_hash": "sha256:OLD", "engine": hh.engine, "input": None},
            {"type": "step_started", "key": "a", "attempt": 1, "idempotency_key": "R:a"},
            {"type": "step_completed", "key": "a", "attempt": 1, "result": 1},
            {"type": "step_started", "key": "b", "attempt": 1, "idempotency_key": "R:b"},
            {"type": "step_completed", "key": "b", "attempt": 1, "result": 2},
        ])
    with fresh(h.engine, "e6") as hh:
        seed(hh)
        r = hh.invoke("run", "l00_linear", input="null")
        expect_code(r, "skew", "E6 changed-hash must refuse (exit 3)")
        expect("flow_hash changed" in ((r.payload or {}).get("error") or ""),
               "E6 refuse payload unclear: %s" % r.payload)
        expect(hh.count_started("a") == 1, "E6 refuse must not touch the journal (no new starts)")
    with fresh(h.engine, "e6b") as hh:
        seed(hh)
        r = hh.invoke("run", "l00_linear", input="null", accept_flow_change=True)
        expect_code(r, "ok", "E6 --accept-flow-change should complete")
        expect(r.payload["result"] == {"sum": 3}, "E6 result: %s" % r.payload)
        expect(hh.count_started("a") == 1 and hh.count_started("b") == 1, "E6 must not re-run completed steps")
        changed = hh.records_of("flow_changed")
        expect(len(changed) == 1 and changed[0]["old_hash"] == "sha256:OLD" and changed[0].get("new_hash"),
               "E6 expected one flow_changed audit record: %s" % changed)
        r2 = hh.invoke("run", "l00_linear", input="null")
        expect_code(r2, "ok", "E6 after acceptance the new hash is current (no flag needed)")
    with fresh(h.engine, "e6c") as hh:
        # A refused resume must NOT consume the --answer (the check runs before apply_answer):
        # the gate stays open, and the same answer + --accept-flow-change then completes.
        hh.seed_journal([
            {"type": "run_started", "run_id": "R", "flow_id": "l02", "flow_version": 1,
             "flow_hash": "sha256:OLD", "engine": hh.engine, "input": None},
            {"type": "ask_requested", "key": "confirm",
             "question": {"prompt": "go?", "type": "boolean"}, "schema": None},
        ])
        r = hh.invoke("resume", "l02_suspend", answer="true")
        expect_code(r, "skew", "E6c changed-hash resume must refuse")
        expect(hh.count_type("ask_answered") == 0, "E6c refused resume consumed the answer")
        r2 = hh.invoke("resume", "l02_suspend", answer="true", accept_flow_change=True)
        expect_code(r2, "ok", "E6c accepted resume should complete")
        expect(hh.count_type("flow_changed") == 1, "E6c expected one flow_changed record")


def lstate(h):
    # state.json is a correct status pointer at suspend and at completion.
    with fresh(h.engine, "state") as hs:
        expect_code(hs.invoke("run", "l02_suspend", input="null"), "suspended", "Lstate run")
        st = json.load(open(os.path.join(hs.state_dir, "state.json"), encoding="utf-8"))
        expect(st["status"] == "suspended" and st["pending"]["key"] == "confirm",
               "Lstate suspended state.json: %s" % st)
        expect_code(hs.invoke("resume", "l02_suspend", answer="true"), "ok", "Lstate resume")
        st2 = json.load(open(os.path.join(hs.state_dir, "state.json"), encoding="utf-8"))
        expect(st2["status"] == "completed" and st2["result"] == {"go": True},
               "Lstate completed state.json: %s" % st2)
    # status == failed
    with fresh(h.engine, "state-fail") as hf:
        hf.invoke("run", "e2_recover", input="null", env={"API_DOWN": "1"})
        sf = json.load(open(os.path.join(hf.state_dir, "state.json"), encoding="utf-8"))
        expect(sf["status"] == "failed" and sf["error"], "Lstate failed state.json: %s" % sf)
    # status == in_doubt (seeded dangling non-idempotent step)
    with fresh(h.engine, "state-doubt") as hd:
        hd.seed_journal([
            {"type": "run_started", "run_id": "R", "flow_id": "l08n", "flow_version": 1,
             "engine": hd.engine, "input": None},
            {"type": "step_started", "key": "act", "attempt": 1, "idempotency_key": "R:act"},
        ])
        hd.invoke("run", "l08n_nonidem", input="null")
        sd = json.load(open(os.path.join(hd.state_dir, "state.json"), encoding="utf-8"))
        expect(sd["status"] == "in_doubt", "Lstate in_doubt state.json: %s" % sd)


# ── Guard / robustness coverage ──────────────────────────────────────────────
def g1(h):
    # True concurrency: two live engines on one state dir. run1 holds the lock while
    # blocked in a step; run2 must be rejected with exit 13 (overlap-proven exclusion).
    with fresh(h.engine, "g1") as hh:
        os.makedirs(hh.state_dir, exist_ok=True)
        go = os.path.join(hh.state_dir, "GO")
        journal = os.path.join(hh.state_dir, "journal.jsonl")
        base = [sys.executable, PY_ENGINE] if h.engine == "py" else ["node", JS_ENGINE]
        argv = base + ["run", "--flow", hh._flow_path("g1_block"),
                       "--state-dir", hh.state_dir, "--input", "null"]
        env = dict(os.environ); env["GO"] = go
        p1 = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
        try:
            for _ in range(500):                 # wait (≤5s) until run1 is fully running
                if os.path.exists(journal):
                    break
                time.sleep(0.01)
            expect(os.path.exists(journal), "G1 run1 never started (no journal)")
            p2 = subprocess.run(argv, capture_output=True, text=True, env=env)
            expect(p2.returncode == EXIT["busy"],
                   "G1 concurrent run must exit 13, got %d (stderr=%s)" % (p2.returncode, p2.stderr.strip()))
        finally:
            open(go, "w").close()                # release run1
            try:
                p1.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p1.kill()
        expect(p1.returncode == EXIT["ok"], "G1 run1 should complete (0), got %s" % p1.returncode)


def g2(h):
    # Re-running and resuming a COMPLETED flow: re-run replays to the same result with no
    # new steps; resume has no pending gate -> usage error (exit 2).
    with fresh(h.engine, "g2") as hh:
        expect_code(hh.invoke("run", "l00_linear", input="null"), "ok", "G2 first run")
        r = hh.invoke("run", "l00_linear", input="null")
        expect_code(r, "ok", "G2 re-run completed flow")
        expect(r.payload["result"] == {"sum": 3}, "G2 re-run result: %s" % r.payload)
        for k in ("a", "b", "c"):
            expect(hh.count_started(k) == 1, "G2 re-run re-executed %s" % k)
        expect_code(hh.invoke("resume", "l00_linear", answer="true"), "usage",
                    "G2 resume of a completed flow -> no pending ask (exit 2)")


def g3(h):
    # Unexpected errors produce a clean {"status":"failed"} (exit 1), not a traceback/crash.
    with fresh(h.engine, "g3a") as ha:
        r = ha.invoke("run", "g3_badresult", input="null")
        expect_code(r, "failed", "G3 non-serializable step result")
        expect(r.payload and r.payload.get("status") == "failed",
               "G3 expected a clean failed payload, got: %s (stderr=%s)" % (r.payload, r.raw))
        expect("oops" in r.payload.get("error", {}).get("message", ""),
               "G3 error should name the offending key 'oops': %s" % r.payload)
    with fresh(h.engine, "g3b") as hb:
        r2 = hb.invoke("run", "g3_gluethrow", input="null")
        expect_code(r2, "failed", "G3 glue exception")
        expect(r2.payload and r2.payload.get("status") == "failed",
               "G3 glue throw must be a clean failed, got: %s (stderr=%s)" % (r2.payload, r2.raw))
        expect(hb.count_started("ok") == 1, "G3 step before the throw should have run")


def g4(h):
    # Structured-object answer: the user supplies a corrected record, not a yes/no.
    with fresh(h.engine, "g4") as hh:
        expect_code(hh.invoke("run", "g4_struct", input="null"), "suspended", "G4 run")
        r = hh.invoke("resume", "g4_struct", answer='{"id":7,"name":"fixed"}')
        expect_code(r, "ok", "G4 resume with structured answer")
        expect(r.payload["result"] == {"fix": {"id": 7, "name": "fixed"}, "applied": True},
               "G4 result: %s" % r.payload)


def g5(h):
    # --no-strict escape hatch: a divergent resume that strict would reject (exit 3) proceeds.
    with fresh(h.engine, "g5") as hh:
        expect_code(hh.invoke("run", "l11_flip", input="null", env={"FLIP": "a"}), "suspended", "G5 run")
        r = hh.invoke("resume", "l11_flip", answer="true", env={"FLIP": "b"}, no_strict=True)
        expect_code(r, "ok", "G5 --no-strict allows the divergent resume")


# ── Error-condition detection ────────────────────────────────────────────────
def d1(h):
    # A malformed line in the MIDDLE of the journal (not the torn tail) -> exit 3.
    with fresh(h.engine, "d1") as hh:
        os.makedirs(hh.state_dir, exist_ok=True)
        jp = os.path.join(hh.state_dir, "journal.jsonl")
        with open(jp, "w", encoding="utf-8") as f:
            f.write(json.dumps({"v": 1, "seq": 0, "ts": "2026-01-01T00:00:00Z", "type": "run_started",
                                "run_id": "R", "flow_id": "l00", "flow_version": 1,
                                "engine": hh.engine, "input": None},
                               sort_keys=True) + "\n")
            f.write("{ this is not valid json }\n")    # corrupt, newline-terminated (not the tail)
            f.write(json.dumps({"v": 1, "seq": 2, "ts": "2026-01-01T00:00:00Z",
                                "type": "step_started", "key": "a", "attempt": 1}, sort_keys=True) + "\n")
        expect_code(hh.invoke("run", "l00_linear", input="null"), "skew", "D1 mid-file corruption -> exit 3")


def d2(h):
    # Malformed --input JSON -> clean usage error (exit 2), not a traceback.
    with fresh(h.engine, "d2") as hh:
        expect_code(hh.invoke("run", "l00_linear", input="{not valid json"), "usage", "D2 bad --input -> exit 2")


def d3(h):
    # A blob whose bytes don't match the recorded sha256 -> exit 3 (no corrupt result returned).
    with fresh(h.engine, "d3") as hh:
        os.makedirs(os.path.join(hh.state_dir, "blobs"), exist_ok=True)
        with open(os.path.join(hh.state_dir, "blobs", "big.1.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps("x" * 100))
        hh.seed_journal([
            {"type": "run_started", "run_id": "R", "flow_id": "l11blob", "flow_version": 1,
             "engine": hh.engine, "input": None},
            {"type": "step_started", "key": "big", "attempt": 1, "idempotency_key": "R:big"},
            {"type": "step_completed", "key": "big", "attempt": 1,
             "result_ref": "big.1.json", "result_sha256": "deadbeefdeadbeef"},
        ])
        expect_code(hh.invoke("run", "l11_blob", input="null"), "skew", "D3 blob sha mismatch -> exit 3")


def d4(h):
    # Answer that violates the ask's schema is rejected (exit 2) and NOT journaled, so a
    # corrected answer still works (the gate stays open).
    with fresh(h.engine, "d4") as hh:
        expect_code(hh.invoke("run", "l_auto_default", input="null"), "suspended", "D4 run")
        expect_code(hh.invoke("resume", "l_auto_default", answer='"notabool"'), "usage",
                    "D4 schema-invalid answer -> exit 2")
        good = hh.invoke("resume", "l_auto_default", answer="false")
        expect_code(good, "ok", "D4 corrected answer after rejection")
        expect(good.payload["result"] == {"go": False}, "D4 result: %s" % good.payload)


def d5(h):
    # An unwritable state dir -> clean error payload (exit 2), NOT a traceback.
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        print("  SKIP d5 (root ignores chmod)")
        return
    base = tempfile.mkdtemp(prefix="ladder-d5-")
    ro = os.path.join(base, "ro")
    os.makedirs(ro)
    os.chmod(ro, 0o500)            # read+execute, no write -> child mkdir fails
    try:
        hh = Harness(h.engine, os.path.join(ro, "flow"))
        r = hh.invoke("run", "l00_linear", input="null")
        expect_code(r, "usage", "D5 unwritable state dir -> exit 2")
        expect(r.payload and r.payload.get("status") == "error",
               "D5 should emit a clean error payload, got: %s" % r.payload)
        expect("    at " not in r.raw, "D5 should not dump a stack trace; stderr=%s" % r.raw)
    finally:
        os.chmod(ro, 0o700)
        shutil.rmtree(base, ignore_errors=True)


# ── Regression rungs for reviewer-found bugs ─────────────────────────────────
def r1(h):
    # Adjudicator SKIP must be memoized: on resume the step is NOT re-run and the
    # adjudicator (an LLM hook) is NOT re-invoked.
    with fresh(h.engine, "r1") as hh:
        expect_code(hh.invoke("run", "r1_adjskip", input="null"), "suspended", "R1 run suspends at gate")
        r2 = hh.invoke("resume", "r1_adjskip", answer="true")
        expect_code(r2, "ok", "R1 resume")
        expect(r2.payload["result"] == {"v": "skipped", "go": True}, "R1 result: %s" % r2.payload)
        adj = [x for x in hh.records_of("ask_answered") if x["key"] == "__adjudicate:risky"]
        expect(len(adj) == 1, "R1 adjudicator re-invoked on resume (%d records)" % len(adj))
        expect(hh.count_started("risky") == 1, "R1 risky re-ran on resume (%d)" % hh.count_started("risky"))
        comp = [x for x in hh.records_of("step_completed") if x["key"] == "risky"]
        expect(comp and comp[0].get("result") == "skipped", "R1 skip not memoized as step_completed")


def r2(h):
    # Prototype-name step keys ("constructor"/"toString") must execute & memoize.
    with fresh(h.engine, "r2") as hh:
        r = hh.invoke("run", "r2_protokey", input="null")
        expect_code(r, "ok", "R2 run")
        expect(r.payload["result"] == {"a": "real-value", "b": "also-real"}, "R2 result: %s" % r.payload)
        expect(hh.count_started("constructor") == 1 and hh.count_started("toString") == 1,
               "R2 proto-keyed step did not execute")
        r2b = hh.invoke("run", "r2_protokey", input="null")
        expect_code(r2b, "ok", "R2 re-run")
        expect(r2b.payload["result"] == {"a": "real-value", "b": "also-real"}, "R2 re-run drift: %s" % r2b.payload)
        expect(hh.count_started("constructor") == 1, "R2 proto-key not memoized on re-run")


def r3(h):
    # A journal written by a NEWER schema (v>SCHEMA_V) -> refuse (exit 3).
    with fresh(h.engine, "r3") as hh:
        os.makedirs(hh.state_dir, exist_ok=True)
        with open(os.path.join(hh.state_dir, "journal.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"v": 2, "seq": 0, "ts": "2026-01-01T00:00:00Z", "type": "run_started",
                                "run_id": "R", "flow_id": "l00", "flow_version": 1,
                                "engine": hh.engine, "input": None}, sort_keys=True) + "\n")
        expect_code(hh.invoke("run", "l00_linear", input="null"), "skew", "R3 newer schema -> exit 3")


def r4(h):
    # --key targeting: a wrong key is rejected (no orphan answer); the right key resumes.
    with fresh(h.engine, "r4") as hh:
        expect_code(hh.invoke("run", "l02_suspend", input="null"), "suspended", "R4 run")
        expect_code(hh.invoke("resume", "l02_suspend", answer="true", key="bogus"), "usage",
                    "R4 wrong --key -> exit 2")
        expect(not any(x["key"] == "bogus" for x in hh.records_of("ask_answered")),
               "R4 wrong --key journaled an orphan answer")
        good = hh.invoke("resume", "l02_suspend", answer="true", key="confirm")
        expect_code(good, "ok", "R4 correct --key resumes")
        expect(good.payload["result"] == {"go": True}, "R4 result: %s" % good.payload)


def r5(h):
    # Glue (outside steps) re-runs every pass; the wrapped step runs once.
    with fresh(h.engine, "r5") as hh:
        os.makedirs(hh.state_dir, exist_ok=True)
        glue = os.path.join(hh.state_dir, "glue.log")
        expect_code(hh.invoke("run", "r5_glue", input="null", env={"GLUE_LOG": glue}), "suspended", "R5 run")
        expect_code(hh.invoke("resume", "r5_glue", answer="true", env={"GLUE_LOG": glue}), "ok", "R5 resume")
        with open(glue, encoding="utf-8") as f:
            lines = [x for x in f.read().split("\n") if x]
        expect(len(lines) == 2, "R5 glue should run on every pass (expected 2, got %d)" % len(lines))
        expect(hh.count_started("work") == 1, "R5 wrapped step should run once")


def r6(h):
    # Positional strict-replay: a real reorder with a shared prefix diverges at #1.
    with fresh(h.engine, "r6") as hh:
        expect_code(hh.invoke("run", "r6_reorder", input="null", env={"ORDER": "normal"}), "suspended", "R6 run")
        bad = hh.invoke("resume", "r6_reorder", answer="true", env={"ORDER": "swapped"})
        expect_code(bad, "skew", "R6 positional reorder -> exit 3")
        expect("request #1" in bad.raw, "R6 expected a positional-divergence message, got: %s" % bad.raw)
    with fresh(h.engine, "r6b") as hb:
        expect_code(hb.invoke("run", "r6_reorder", input="null", env={"ORDER": "normal"}), "suspended", "R6b run")
        ok = hb.invoke("resume", "r6_reorder", answer="true", env={"ORDER": "swapped"}, no_strict=True)
        expect_code(ok, "ok", "R6 --no-strict allows the reorder (memo hits)")


def inresolve(h):
    # In-doubt resolution: the orchestrator resolves a non-idempotent interrupted step
    # via `resume --resolve completed|retry|abort` (closes the exit-11 dead end).
    def seed(hh):
        hh.seed_journal([
            {"type": "run_started", "run_id": "R", "flow_id": "l08n", "flow_version": 1,
             "engine": hh.engine, "input": None},
            {"type": "step_started", "key": "act", "attempt": 1, "idempotency_key": "R:act"},
        ])
    # completed: the effect DID land -> synthesize completion with the observed value, no re-run
    with fresh(h.engine, "inr-c") as hc:
        seed(hc)
        expect_code(hc.invoke("run", "l08n_nonidem", input="null"), "in_doubt", "INr escalates first")
        r = hc.invoke("resume", "l08n_nonidem", resolve="completed", resolve_value='"external"')
        expect_code(r, "ok", "INr resolve completed")
        expect(r.payload["result"] == {"v": "external"}, "INr completed result: %s" % r.payload)
        expect(hc.count_started("act") == 1, "INr completed must NOT re-run (%d)" % hc.count_started("act"))
        comp = [x for x in hc.records_of("step_completed") if x["key"] == "act"]
        expect(comp and comp[0].get("result") == "external", "INr completion not synthesized")
        resolved = [x for x in hc.records_of("in_doubt_resolved") if x["key"] == "act"]
        expect(resolved and resolved[0]["action"] == "completed" and resolved[0]["value"] == "external",
               "INr in_doubt_resolved record shape wrong: %s" % resolved)
    # completed with NO --resolve-value: defaults to null (cli-contract.md §In-doubt)
    with fresh(h.engine, "inr-cn") as hn:
        seed(hn)
        expect_code(hn.invoke("run", "l08n_nonidem", input="null"), "in_doubt", "INr null escalates")
        rn = hn.invoke("resume", "l08n_nonidem", resolve="completed")
        expect_code(rn, "ok", "INr resolve completed, no value")
        expect(rn.payload["result"] == {"v": None}, "INr default-null result: %s" % rn.payload)
        resolved_n = [x for x in hn.records_of("in_doubt_resolved") if x["key"] == "act"]
        expect(resolved_n and resolved_n[0]["action"] == "completed" and resolved_n[0].get("value") is None,
               "INr default-null in_doubt_resolved wrong: %s" % resolved_n)
    # retry: the effect did NOT land -> re-run the step once
    with fresh(h.engine, "inr-r") as hr:
        seed(hr)
        r = hr.invoke("resume", "l08n_nonidem", resolve="retry")
        expect_code(r, "ok", "INr resolve retry")
        expect(r.payload["result"] == {"v": "done"}, "INr retry result: %s" % r.payload)
        expect(hr.count_started("act") == 2, "INr retry must re-run (%d)" % hr.count_started("act"))
    # abort: terminal failure
    with fresh(h.engine, "inr-a") as ha:
        seed(ha)
        expect_code(ha.invoke("resume", "l08n_nonidem", resolve="abort"), "failed", "INr resolve abort -> exit 1")
    # a wrong --resolve-key is rejected
    with fresh(h.engine, "inr-k") as hk:
        seed(hk)
        expect_code(hk.invoke("resume", "l08n_nonidem", resolve="completed", resolve_key="nope"),
                    "usage", "INr wrong --resolve-key -> exit 2")


def inoptions(h):
    # exit-11 payload contract: options are exactly the CLI --resolve verbs, and state.json's
    # pending mirrors stdout's byte-for-byte (they used to diverge).
    h.seed_journal([
        {"type": "run_started", "run_id": "R", "flow_id": "l08n", "flow_version": 1,
         "engine": h.engine, "input": None},
        {"type": "step_started", "key": "act", "attempt": 1, "idempotency_key": "R:act"},
    ])
    r = h.invoke("run", "l08n_nonidem", input="null")
    expect_code(r, "in_doubt", "INOPT run")
    p = r.payload["pending"]
    expect(p.get("options") == ["completed", "retry", "abort"],
           "INOPT options must be the CLI verbs: %s" % p)
    expect(p.get("key") == "act" and p.get("interrupted_step") == "act" and p.get("attempt") == 1,
           "INOPT pending fields wrong: %s" % p)
    st = json.load(open(os.path.join(h.state_dir, "state.json"), encoding="utf-8"))
    expect(st["pending"] == p, "INOPT state.json pending must mirror stdout: %s" % st["pending"])


# ── Rich intervention library: QUARANTINED to extras/intervention/ (see its README) ──
# The spec interpreter never imports intervene.py; its interrupt->enrich loop is built in.


def obs(h):
    # observer fires before/after on fresh work, replay on memo-hits, ask on a gate;
    # an observer that throws must NOT fail the flow.
    with fresh(h.engine, "obs") as hh:
        os.makedirs(hh.state_dir, exist_ok=True)
        log = os.path.join(hh.state_dir, "obs.log")

        def events():
            with open(log, encoding="utf-8") as f:
                return [json.loads(x) for x in f.read().splitlines() if x]

        r = hh.invoke("run", "obs1_observer", input="null", env={"OBS_LOG": log})
        expect_code(r, "suspended", "OBS run")
        ev1 = events()
        expect({"phase": "before", "key": "a"} in ev1 and {"phase": "after", "key": "a"} in ev1,
               "OBS run missing before/after(a): %s" % ev1)
        expect({"phase": "ask", "key": "gate"} in ev1, "OBS run missing ask(gate): %s" % ev1)
        expect(not any(e["phase"] == "before" and e["key"] == "b" for e in ev1),
               "OBS 'b' ran before the suspend: %s" % ev1)
        n1 = len(ev1)
        r2 = hh.invoke("resume", "obs1_observer", answer="true", env={"OBS_LOG": log})
        expect_code(r2, "ok", "OBS resume")
        new = events()[n1:]
        expect({"phase": "replay", "key": "a"} in new, "OBS resume missing replay(a): %s" % new)
        expect({"phase": "before", "key": "b"} in new and {"phase": "after", "key": "b"} in new,
               "OBS resume missing before/after(b): %s" % new)
        expect(r2.payload["result"] == {"a": 1, "b": 2, "go": True}, "OBS result: %s" % r2.payload)
    # a throwing observer must not fail the flow
    with fresh(h.engine, "obs-throw") as ht:
        r = ht.invoke("run", "obs2_throws", input="null", env={"OBS_LOG": os.devnull})
        expect_code(r, "ok", "OBS throwing observer must not fail the flow")
        expect(r.payload["result"] == {"v": 1}, "OBS throw result: %s" % r.payload)
    # a step that fails then retries fires a 'failed' event (and still completes)
    with fresh(h.engine, "obs-fail") as hf:
        os.makedirs(hf.state_dir, exist_ok=True)
        log = os.path.join(hf.state_dir, "obs.log")
        r = hf.invoke("run", "obs3_fail", input="null", env={"OBS_LOG": log})
        expect_code(r, "ok", "OBS3 completes after retry")
        evs = [json.loads(x) for x in open(log, encoding="utf-8").read().splitlines() if x]
        expect({"phase": "failed", "key": "flaky"} in evs, "OBS3 missing failed event: %s" % evs)
        expect({"phase": "after", "key": "flaky"} in evs, "OBS3 missing after event: %s" % evs)


# ── Workflow interpreter (data-defined, durable, LLM-authored) ───────────────
def wf_state(h):
    # flowing pipe + named global (set/append) + auto-store under $.<id> + `desc` journaling.
    r = h.invoke("run", "wf_state", input="null")
    expect_code(r, "ok", "wf_state run")
    res = r.payload["result"]
    expect(res["result"] == {"saw_kept": 7, "saw_flow": 7, "saw_autostore": 1},
           "wf_state flowing/threading wrong: %s" % res)
    expect(res["state"]["kept"] == 7 and res["state"]["audit"] == [7],
           "wf_state global state wrong: %s" % res["state"])
    expect(res["state"]["a"] == {"val": 7, "extra": 1},
           "wf_state auto-store under $.a wrong: %s" % res["state"])
    started = [x for x in h.records_of("step_started") if x.get("key") == "a#0"]
    expect(started and started[0].get("desc") == "make a value",
           "wf_state intent not journaled as desc: %s" % started)


def wf_route(h):
    rb = h.invoke("run", "wf_route", input='{"kind":"big"}')
    expect_code(rb, "ok", "wf_route big")
    expect(rb.payload["result"]["result"] == {"size": "BIG"}, "wf_route big result: %s" % rb.payload)
    expect(h.has_started("big#0") and not h.has_started("small#0"), "wf_route predicate branch wrong")
    with fresh(h.engine, "wfroute-s") as hs:
        rs = hs.invoke("run", "wf_route", input='{"kind":"small"}')
        expect_code(rs, "ok", "wf_route small")
        expect(rs.payload["result"]["result"] == {"size": "small"}, "wf_route small result: %s" % rs.payload)
        expect(hs.has_started("small#0") and not hs.has_started("big#0"), "wf_route default branch wrong")
    with fresh(h.engine, "wfroute-h") as hh:
        # third `when.if` form: a registry predicate fn `(state, result) -> bool`
        rh = hh.invoke("run", "wf_route", input='{"kind":"huge"}')
        expect_code(rh, "ok", "wf_route huge (predicate fn)")
        expect(rh.payload["result"]["result"] == {"size": "HUGE"}, "wf_route huge result: %s" % rh.payload)
        expect(hh.has_started("huge#0") and not hh.has_started("big#0") and not hh.has_started("small#0"),
               "wf_route predicate-fn branch wrong")


def wf_return(h):
    # prompt: substitution, structured return, validate->repair (bad `next` once), label route, set.
    r = h.invoke("run", "wf_return", input='{"amount":5}')
    expect_code(r, "ok", "wf_return run")
    expect(r.payload["result"]["result"] == {"ok": True, "decision": "approve"},
           "wf_return result: %s" % r.payload)
    expect(r.payload["result"]["state"]["decision"] == "approve", "wf_return set mutation lost")
    expect(h.count_started("assess#0/llm#0") == 1 and h.count_started("assess#0/llm#1") == 1,
           "wf_return repair attempts wrong (llm#0=%d llm#1=%d)"
           % (h.count_started("assess#0/llm#0"), h.count_started("assess#0/llm#1")))
    expect(h.count_started("assess#0/route#0") == 0,
           "wf_return declared outcome must route with ZERO judge calls")
    expect(r.payload["result"]["state"]["assess"] == {"verdict": "approve", "x": 1,
                                                      "outcome": "approve"},
           "wf_return parsed task output wrong: %s" % r.payload["result"]["state"].get("assess"))
    starts = len(h.records_of("step_started"))
    r2 = h.invoke("run", "wf_return", input='{"amount":5}')
    expect_code(r2, "ok", "wf_return re-run")
    expect(len(h.records_of("step_started")) == starts, "wf_return re-ran a memoized step")


def wf_context(h):
    # conversational context: SHARED by default — one flow-wide thread (2, then 4 = first's exchange
    # visible); step-level "isolated" opts a step out (2); a loop revisit CONTINUES the thread and
    # GATES ARE TURNS (fourth#1 = 10: three exchanges + the gate's Q&A); an isolated gate leaves the
    # thread untouched (isogate variant: fourth#1 = 8); spec-level context:"isolated" (lean variant)
    # makes every step 2. history_len counts [system] + thread + [user].
    r = h.invoke("run", "wf_context", input="null")
    expect_code(r, "suspended", "wf_context run reaches the gate")
    r2 = h.invoke("resume", "wf_context", answer='"again"')
    expect_code(r2, "suspended", "wf_context loops back to fourth then the gate again")
    r3 = h.invoke("resume", "wf_context", answer='"done"')
    expect_code(r3, "ok", "wf_context completes")
    state = r3.payload["result"]["state"]
    expect(state["first"] == {"history_len": 2},
           "wf_context first opens the flow thread: %s" % state.get("first"))
    expect(state["second"] == {"history_len": 4},
           "wf_context second must see first's exchange (shared default): %s" % state.get("second"))
    expect(state["third"] == {"history_len": 2},
           "wf_context third ('isolated' override) must NOT see the thread: %s" % state.get("third"))
    expect(state["fourth"] == {"history_len": 10},
           "wf_context fourth#1 (loop revisit) must CONTINUE the thread incl. the GATE'S TURNS "
           "(first+second+fourth#0 exchanges + gate Q&A): %s" % state.get("fourth"))
    with fresh(h.engine, "wfctx-ig") as hg:
        env = {"WF_CONTEXT": "isogate"}
        expect_code(hg.invoke("run", "wf_context", input="null", env=env), "suspended",
                    "wf_context isogate run")
        expect_code(hg.invoke("resume", "wf_context", answer='"again"', env=env), "suspended",
                    "wf_context isogate loop")
        rg = hg.invoke("resume", "wf_context", answer='"done"', env=env)
        expect_code(rg, "ok", "wf_context isogate completes")
        expect(rg.payload["result"]["state"]["fourth"] == {"history_len": 8},
               "wf_context an ISOLATED gate must leave the thread untouched: %s"
               % rg.payload["result"]["state"]["fourth"])
    with fresh(h.engine, "wfctx-lean") as hl:
        rl = hl.invoke("run", "wf_context", input="null", env={"WF_CONTEXT": "lean"})
        expect_code(rl, "suspended", "wf_context lean run")
        rl2 = hl.invoke("resume", "wf_context", answer='"done"', env={"WF_CONTEXT": "lean"})
        expect_code(rl2, "ok", "wf_context lean completes")
        st = rl2.payload["result"]["state"]
        expect(all(st[k] == {"history_len": 2} for k in ("first", "second", "third", "fourth")),
               "wf_context spec-level isolated must make every step lean: %s"
               % {k: st[k] for k in ("first", "second", "third", "fourth")})


def wf_decide(h):
    r = h.invoke("run", "wf_decide", input='{"amount":9}')
    expect_code(r, "suspended", "wf_decide run")
    expect(r.payload["pending"]["key"] == "review#0", "wf_decide gate key: %s" % r.payload)
    expect('{"amount":9}' in r.payload["pending"]["question"]["prompt"],
           "wf_decide substitution: %s" % r.payload["pending"]["question"])
    r2 = h.invoke("resume", "wf_decide", answer='"approve"')
    expect_code(r2, "ok", "wf_decide approve")
    expect(r2.payload["result"]["result"] == {"approved": True, "decision": "approve"},
           "wf_decide result: %s" % r2.payload)
    with fresh(h.engine, "wfdecide-d") as hd:
        expect_code(hd.invoke("run", "wf_decide", input='{"amount":9}'), "suspended", "wf_decide deny run")
        expect_code(hd.invoke("resume", "wf_decide", answer='"deny"'), "failed", "wf_decide deny -> @fail")


def wf_abort(h):
    r = h.invoke("run", "wf_abort", input='{"bad":true}')
    expect_code(r, "failed", "wf_abort @fail -> failed")
    with fresh(h.engine, "wfabort-ok") as ho:
        ro = ho.invoke("run", "wf_abort", input='{"bad":false}')
        expect_code(ro, "ok", "wf_abort pass")
        expect(ro.payload["result"]["result"] == {"bad": False}, "wf_abort pass result: %s" % ro.payload)


def wf_intervene(h):
    # enriched-context interruptibility: decision-request -> human gate -> answer woven into the convo -> resolve.
    r = h.invoke("run", "wf_intervene", input='{"amount":5}')
    expect_code(r, "suspended", "wf_intervene run")
    expect(r.payload["pending"]["key"] == "assess#0/intervene#0",
           "wf_intervene gate key: %s" % r.payload["pending"]["key"])
    r2 = h.invoke("resume", "wf_intervene", answer='"approve"')
    expect_code(r2, "ok", "wf_intervene resume")
    expect(r2.payload["result"]["result"] == {"resolved": True}, "wf_intervene result: %s" % r2.payload)
    expect(r2.payload["result"]["state"]["via"] == "human", "wf_intervene enrichment lost: %s" % r2.payload)
    expect(h.count_started("assess#0/llm#0") == 1 and h.count_started("assess#0/llm#1") == 1,
           "wf_intervene call counts wrong (llm#0=%d llm#1=%d)"
           % (h.count_started("assess#0/llm#0"), h.count_started("assess#0/llm#1")))
    starts = len(h.records_of("step_started"))
    r3 = h.invoke("run", "wf_intervene", input='{"amount":5}')
    expect_code(r3, "ok", "wf_intervene re-run")
    expect(len(h.records_of("step_started")) == starts, "wf_intervene re-ran a memoized step")


def wf_intervene_multi(h):
    # MULTI-round reentrancy: two separate suspend/resume round-trips before the third call
    # resolves. Each earlier round's model call must fire exactly once, TOTAL, across the
    # whole chain — a later suspend/resume must never re-invoke an already-completed round.
    r = h.invoke("run", "wf_intervene_multi", input="null")
    expect_code(r, "suspended", "wfim round0 suspends")
    expect(r.payload["pending"]["key"] == "assess#0/intervene#0",
           "wfim expected first gate: %s" % r.payload["pending"]["key"])
    expect(h.count_started("assess#0/llm#0") == 1, "wfim round0 must have run once")

    r2 = h.invoke("resume", "wf_intervene_multi", answer='"go"')
    expect_code(r2, "suspended", "wfim round1 suspends")
    expect(r2.payload["pending"]["key"] == "assess#0/intervene#1",
           "wfim expected second gate: %s" % r2.payload["pending"]["key"])
    # THE key assertion: resuming into round 1 must NOT re-invoke round 0's memoized call.
    expect(h.count_started("assess#0/llm#0") == 1,
           "wfim round0 was RE-INVOKED across the second suspend/resume (%d)"
           % h.count_started("assess#0/llm#0"))
    expect(h.count_started("assess#0/llm#1") == 1, "wfim round1 must have run once")

    r3 = h.invoke("resume", "wf_intervene_multi", answer='"go"')
    expect_code(r3, "ok", "wfim resolves on round2")
    expect(r3.payload["result"]["result"] == {"resolved": True, "rounds": 2},
           "wfim result: %s" % r3.payload)
    for k in ("assess#0/llm#0", "assess#0/llm#1", "assess#0/llm#2"):
        expect(h.count_started(k) == 1,
               "wfim %s must have run EXACTLY once across the whole chain (%d)"
               % (k, h.count_started(k)))

    # A full fresh re-run replays the ENTIRE multi-round dialogue with zero new step_starts.
    starts = len(h.records_of("step_started"))
    r4 = h.invoke("run", "wf_intervene_multi", input="null")
    expect_code(r4, "ok", "wfim re-run")
    expect(r4.payload["result"] == r3.payload["result"], "wfim re-run result drift")
    expect(len(h.records_of("step_started")) == starts,
           "wfim re-run re-executed part of the memoized multi-round dialogue")


def wf_paths(h):
    # end-to-end: index + missing->"" + nested + $${ escape in a rendered prompt; lone-ref keeps number type.
    r = h.invoke("run", "wf_paths", input='{"items":[{"name":"a"},{"name":"b"}],"price":42}')
    expect_code(r, "suspended", "wf_paths run")
    q = r.payload["pending"]["question"]["prompt"]
    expect(q == "item=b missing=[] esc=${x} price=42", "wf_paths render wrong: %r" % q)
    r2 = h.invoke("resume", "wf_paths", answer='"go"')
    expect_code(r2, "ok", "wf_paths resume")
    saved = r2.payload["result"]["state"]["saved"]
    expect(saved == 42 and isinstance(saved, int) and not isinstance(saved, bool),
           "wf_paths lone-ref did not preserve the number type: %r" % saved)


def wf_search(h):
    # search kind: injected caller returns structured results; route on $.<step>.results[0].url; memoized once.
    r = h.invoke("run", "wf_search", input='{"topic":"acme"}')
    expect_code(r, "ok", "wf_search run")
    res = r.payload["result"]
    expect(res["result"]["top"] == "https://ex.com/refunds",
           "wf_search did not route on the results URL: %s" % res)
    expect(res["result"]["q"] == "acme latest", "wf_search query interpolation wrong: %s" % res["result"])
    expect(res["result"]["fmt"] == "structured", "wf_search format not passed to caller: %s" % res["result"])
    expect(res["state"]["research"]["results"][0]["title"] == "Refund policy",
           "wf_search result not auto-stored at $.research: %s" % res["state"].get("research"))
    expect(h.count_started("research#0") == 1, "wf_search step not memoized once")
    starts = len(h.records_of("step_started"))
    r2 = h.invoke("run", "wf_search", input='{"topic":"acme"}')
    expect_code(r2, "ok", "wf_search re-run")
    expect(len(h.records_of("step_started")) == starts, "wf_search re-ran a memoized step")


def wf_map(h):
    # map kind: inner `run` + reduce fold (proving $.it / $.it_index bindings), then inner `prompt` fan-out.
    r = h.invoke("run", "wf_map", input='{"items":[{"name":"a"},{"name":"b"}]}')
    expect_code(r, "ok", "wf_map run")
    res = r.payload["result"]
    expect(res["state"]["fan"] == {"labels": ["A", "B"], "idxs": [0, 1]},
           "wf_map reduce/binding wrong: %s" % res["state"].get("fan"))
    outs = res["result"]                       # no reduce on `fanp` -> the ordered per-item list flows out
    expect(isinstance(outs, list) and len(outs) == 2,
           "wf_map fanp did not flow the per-item list: %s" % outs)
    expect("Summarize a." in outs[0]["echo"] and "Summarize b." in outs[1]["echo"],
           "wf_map inner prompt did not render per-item ${$.it.name}: %s" % outs)
    for k in ("fan#0/map#0", "fan#0/map#1", "fan#0/reduce", "fanp#0/map#0/llm#0", "fanp#0/map#1/llm#0"):
        expect(h.count_started(k) == 1, "wf_map step %s not started exactly once (%d)" % (k, h.count_started(k)))
    starts = len(h.records_of("step_started"))
    r2 = h.invoke("run", "wf_map", input='{"items":[{"name":"a"},{"name":"b"}]}')
    expect_code(r2, "ok", "wf_map re-run")
    expect(len(h.records_of("step_started")) == starts, "wf_map re-ran a memoized step")


def wf_mapsusp(h):
    # a step INSIDE a map suspends per item; resume memoizes answered items (asks item 1, not item 0 again).
    r = h.invoke("run", "wf_mapsusp", input='{"xs":["a","b"]}')
    expect_code(r, "suspended", "wf_mapsusp run")
    expect(r.payload["pending"]["key"] == "gate_each#0/map#0",
           "wf_mapsusp first gate wrong: %s" % r.payload["pending"])
    r2 = h.invoke("resume", "wf_mapsusp", answer='"approve"')
    expect_code(r2, "suspended", "wf_mapsusp second gate")
    expect(r2.payload["pending"]["key"] == "gate_each#0/map#1",
           "wf_mapsusp did not advance to item 1 (item 0 re-asked?): %s" % r2.payload["pending"])
    r3 = h.invoke("resume", "wf_mapsusp", answer='"reject"')
    expect_code(r3, "ok", "wf_mapsusp final resume")
    outs = r3.payload["result"]["result"]
    expect(outs == [{"decision": "approve"}, {"decision": "reject"}],
           "wf_mapsusp collected per-item decisions wrong: %s" % outs)


def wf_mapedge(h):
    # empty list: zero item steps but reduce still runs; custom `as` + nested `over` + $.<as>_index.
    r = h.invoke("run", "wf_mapedge", input='{"none":[],"data":{"items":[{"id":"p"},{"id":"q"}]}}')
    expect_code(r, "ok", "wf_mapedge run")
    res = r.payload["result"]
    expect(res["state"]["empty"] == {"n": 0}, "wf_mapedge reduce over [] wrong: %s" % res["state"].get("empty"))
    expect(h.count_started("empty#0/map#0") == 0, "wf_mapedge phantom-iterated an empty list")
    expect(h.count_started("empty#0/reduce") == 1, "wf_mapedge reduce did not run on empty list")
    expect(res["result"] == [{"who": "p", "i": 0}, {"who": "q", "i": 1}],
           "wf_mapedge custom-as / nested-over / index wrong: %s" % res["result"])


def wf_mapbad(h):
    # `over` that is not a list -> clean failure (exit 1) with a clear message, not a crash.
    r = h.invoke("run", "wf_mapbad", input='{"notalist":5}')
    expect_code(r, "failed", "wf_mapbad should fail on a non-list `over`")
    msg = ((r.payload or {}).get("error") or {}).get("message", "")
    expect("did not resolve to a list" in msg, "wf_mapbad error message unclear: %s" % r.payload)


def wf_specbad(h):
    # spec hardening rejects at LOAD (usage exit + clear stderr, no journal): pure-fan-out keys on
    # a map `do`, a used kind whose caller was not injected, a bad search `format`, `as` shadowing,
    # and malformed failure policy (on_error/on_exhausted/idempotent shape + placement).
    cases = [("inner_routes", "cannot carry routes"),
             ("no_llm", "needs an llm caller"),
             ("bad_format", "must be \"structured\""),
             ("as_input", "collides with `input` or a state name"),
             ("onerr_prompt", "on_error is only allowed on run/search states"),
             ("onerr_badto", "on_error `to` routes to unknown target"),
             ("onerr_empty", "on_error needs at least one rule"),
             ("onerr_badregex", "bad on_error match regex"),
             ("onerr_inlineflags", "bad on_error match regex"),
             ("inner_onerr", "cannot carry on_error"),
             ("exh_run", "on_exhausted is no longer supported"),
             ("idem_search", "`idempotent` is only allowed on run states"),
             ("nested_map", "map `do` cannot itself be a map"),
             ("reduce_kind", "map `reduce` must be a `run` step"),
             ("reduce_routes", "cannot carry routes"),
             ("bad_namespace", "no longer a supported spec key"),
             ("bad_maxvisits", "no longer a supported spec key"),
             ("pred_twoops", "exactly one operator"),
             ("pred_badop", "unknown key(s)"),
             ("ask_unmapped", "not mapped in `routes`"),
             ("bad_spec_context", "spec `context` must be \"shared\" or \"isolated\""),
             ("dead_next", "`next` is unreachable"),
             ("flow_kind", "must have exactly one kind"),
             ("agent_kind", "must have exactly one kind")]
    for tag, needle in cases:
        r = h.invoke("run", "wf_specbad", input="{}", env={"WF_SPECBAD": tag})
        expect_code(r, "usage", "wf_specbad %s should be rejected at load" % tag)
        expect(needle in r.raw, "wf_specbad %s stderr unclear: %s" % (tag, r.raw))
        expect(r.payload is None, "wf_specbad %s emitted a run payload despite load failure" % tag)
    expect(h.journal() == [], "wf_specbad load failures must not touch the journal")


def l_inhash(h):
    # engine in-hash memo validity: same hash -> replay (fn not run); changed hash ->
    # memo_invalidated journaled, stale key_order tail truncated, step re-executes, newest wins;
    # replay of the NEW record is stable; legacy (no in_hash) steps untouched throughout.
    r = h.invoke("run", "l_inhash", input="null", env={"INHASH": "A"})
    expect_code(r, "ok", "l_inhash first run")
    expect(r.payload["result"]["got"] == {"v": "A"}, "l_inhash first result: %s" % r.payload)
    r2 = h.invoke("run", "l_inhash", input="null", env={"INHASH": "A"})
    expect_code(r2, "ok", "l_inhash same-hash re-run")
    expect(h.count_started("work") == 1, "l_inhash same hash must REPLAY (started=%d)"
           % h.count_started("work"))
    r3 = h.invoke("run", "l_inhash", input="null", env={"INHASH": "B"})
    expect_code(r3, "ok", "l_inhash changed-hash re-run")
    expect(r3.payload["result"]["got"] == {"v": "B"}, "l_inhash newest must win: %s" % r3.payload)
    expect(h.count_started("work") == 2, "l_inhash changed hash must RE-EXECUTE (started=%d)"
           % h.count_started("work"))
    inv = [x for x in h.journal() if x.get("type") == "memo_invalidated"]
    expect(len(inv) == 1 and inv[0]["key"] == "work" and inv[0]["new_hash"] == "sha256:B",
           "l_inhash memo_invalidated record wrong: %s" % inv)
    expect(h.count_started("after") == 1 and r3.payload["result"]["after"] == {"w": "A!"},
           "l_inhash non-hashed steps keep their memos across an invalidation (the documented "
           "v1 boundary: only declared-input steps cascade): started=%d payload=%s"
           % (h.count_started("after"), r3.payload["result"]))
    r4 = h.invoke("run", "l_inhash", input="null", env={"INHASH": "B"})
    expect_code(r4, "ok", "l_inhash stable replay of the new record")
    expect(h.count_started("work") == 2 and len(
        [x for x in h.journal() if x.get("type") == "memo_invalidated"]) == 1,
           "l_inhash replay after invalidation must be quiet")


def wf_rehash(h):
    # the edit-while-parked scenario: resume under an EDITED definition -> only the calls whose
    # rendered conversations changed re-execute (cascade follows OUTPUTS), the untouched prefix
    # replays, and the human's answer is never re-asked.
    r = h.invoke("run", "wf_rehash", input='"x"', env={"WF_REHASH": "v1"})
    expect_code(r, "suspended", "wf_rehash run parks at the gate")
    expect(r.payload["pending"]["key"] == "gate#0", "wf_rehash gate key: %s" % r.payload["pending"])
    r2 = h.invoke("resume", "wf_rehash", answer='"ok"', env={"WF_REHASH": "v2"})
    expect_code(r2, "ok", "wf_rehash resume under the EDITED template completes")
    expect("thoroughly" in r2.payload["result"]["result"]["seen"],
           "wf_rehash outcome must reflect the NEW prompt: %s" % r2.payload["result"]["result"])
    expect(h.count_started("pre#0/llm#0") == 1, "wf_rehash untouched pre must replay")
    expect(h.count_started("edit#0/llm#0") == 2, "wf_rehash edited task must re-execute")
    expect(h.count_started("edit#0/route#0") == 0, "wf_rehash fast path: no judge calls")
    expect(h.count_started("post#0/llm#0") == 2,
           "wf_rehash downstream post must CASCADE (its rendered input changed)")
    expect(len([x for x in h.journal() if x.get("type") == "ask_requested"
                and x.get("key") == "gate#0"]) == 1,
           "wf_rehash the human's answer must survive (never re-asked)")
    with fresh(h.engine, "wfrh-m") as hm:
        rm = hm.invoke("run", "wf_rehash", input='"x"', env={"WF_REHASH_MEANS": "m1"})
        expect_code(rm, "suspended", "wf_rehash means run")
        rm2 = hm.invoke("resume", "wf_rehash", answer='"ok"', env={"WF_REHASH_MEANS": "m2"})
        expect_code(rm2, "ok", "wf_rehash means-edit resume completes")
        expect(hm.count_started("edit#0/llm#0") == 2 and hm.count_started("edit#0/route#0") == 0,
               "wf_rehash means edit must re-run the task (the prefixed contract changed); no judge")
        expect(hm.count_started("post#0/llm#0") == 1,
               "wf_rehash means edit: identical task OUTPUT -> post must stay memoized")


def wf_seq(h):
    # SEQUENTIAL FALL-THROUGH: unrouted states proceed in declaration order; the last declared state
    # falls to @done; an UNMAPPED ask answer falls onward only because the step is "optional": true;
    # a mapped answer still routes (binding).
    r = h.invoke("run", "wf_seq", input="null")
    expect_code(r, "suspended", "wf_seq run reaches the b gate via fall-through")
    expect(r.payload["pending"]["key"] == "b#0", "wf_seq gate key: %s" % r.payload["pending"])
    r2 = h.invoke("resume", "wf_seq", answer='"go"')
    expect_code(r2, "ok", "wf_seq 'go' (unmapped, optional) falls onward and completes")
    state = r2.payload["result"]["state"]
    expect("c" in state and state["z"] == {"done": True},
           "wf_seq must fall through c then z then @done: %s" % sorted(state))
    with fresh(h.engine, "wfseq-b") as hb:
        expect_code(hb.invoke("run", "wf_seq", input="null"), "suspended", "wf_seq (special) run")
        rb = hb.invoke("resume", "wf_seq", answer='"special"')
        expect_code(rb, "ok", "wf_seq 'special' routes explicitly")
        expect("c" not in rb.payload["result"]["state"],
               "wf_seq 'special' must SKIP c (mapped route beats fall-through): %s"
               % sorted(rb.payload["result"]["state"]))


def wf_cycle(h):
    # cycles: a revise answer loops BACK to write (visit keys write#0/write#1); the write template
    # reads ${$.feedback.decision} — empty brackets on lap 0, the human's text on lap 1; authored
    # `append` collects one entry per lap; resume replays lap 0 without re-calling the stub
    # (write#0/llm#0 started exactly once across the whole chain). The runaway variant (WF_CYCLE=
    # runaway) is a prompt<->prompt cycle that must die at the fixed visit cap with a clear message.
    r = h.invoke("run", "wf_cycle", input="null")
    expect_code(r, "suspended", "wf_cycle run suspends at review")
    r2 = h.invoke("resume", "wf_cycle", answer='"revise"')
    expect_code(r2, "suspended", "wf_cycle revise suspends at feedback")
    expect(r2.payload["pending"]["key"] == "feedback#0", "wf_cycle feedback key: %s" % r2.payload["pending"])
    r3 = h.invoke("resume", "wf_cycle", answer='"add a summary"')
    expect_code(r3, "suspended", "wf_cycle loops back to write then review#1")
    expect(r3.payload["pending"]["key"] == "review#1",
           "wf_cycle second review visit key: %s" % r3.payload["pending"])
    r4 = h.invoke("resume", "wf_cycle", answer='"ship"')
    expect_code(r4, "ok", "wf_cycle ships on lap 1")
    laps = r4.payload["result"]["state"]["laps"]
    expect(len(laps) == 2 and "[]" in laps[0] and "add a summary" in laps[1],
           "wf_cycle lap prompts must show empty-then-filled feedback: %s" % laps)
    expect(h.count_started("write#0/llm#0") == 1,
           "wf_cycle lap-0 model call must be memoized across resumes")
    with fresh(h.engine, "wfcycle-r") as hr:
        rr = hr.invoke("run", "wf_cycle", input="null", env={"WF_CYCLE": "runaway"})
        expect_code(rr, "failed", "wf_cycle runaway must die at the visit cap")
        msg = ((rr.payload.get("error") or {}).get("message") or "")
        expect("visit cap of 25" in msg,
               "wf_cycle runaway needs the clear cap message: %s" % rr.payload)


def wf_scaffold(h):
    # the engine-owned scaffolding contract: ONE leading system message (ASK rule + return contract +
    # legal labels when routed), the author's directive as a PURE final user message (byte-exact,
    # nothing appended), the ASK: string convention works on an UNROUTED step, and the resume turn
    # uses the standardized wording.
    r = h.invoke("run", "wf_scaffold", input='{"n": 42}')
    expect_code(r, "suspended", "wf_scaffold gated step must ASK via the string convention")
    expect(r.payload["pending"]["key"] == "gated#0/intervene#0",
           "wf_scaffold gate key: %s" % r.payload["pending"])
    expect(r.payload["pending"]["question"]["prompt"] == "what now?",
           "wf_scaffold ASK question: %s" % r.payload["pending"]["question"])
    r2 = h.invoke("resume", "wf_scaffold", answer='"ok"')
    expect_code(r2, "ok", "wf_scaffold resume completes")
    probe = r2.payload["result"]["state"]["probe"]
    expect(probe["sys_role"] == "system" and probe["n_msgs"] == 2,
           "wf_scaffold convo shape [system, user]: %s" % probe)
    expect(probe["sys_has_ask_rule"] and probe["sys_has_json_rule"] and probe["sys_has_outcomes"]
           and probe["sys_contract_leads"],
           "wf_scaffold system prefix must LEAD with the outcome contract + carry ASK/JSON rules: %s"
           % probe)
    expect(h.count_started("probe#0/route#0") == 0,
           "wf_scaffold declared outcome must route WITHOUT the judge (fast path)")
    expect(probe["user_exact"] == "Probe 42.",
           "wf_scaffold user directive must be byte-exact (nothing appended): %r" % probe["user_exact"])
    weave = r2.payload["result"]["state"]["gated"]["weave"]
    expect(weave == "The human answered: ok. Continue the instruction with this information.",
           "wf_scaffold standardized resume turn: %r" % weave)


def wf_router(h):
    # the independent edge judge: binding-by-default (illegal proceed -> repair -> label), optional
    # proceed -> fall-through, reasoned ask -> feedback -> task re-attempt -> fresh judgment, repair
    # exhaustion -> the FORCED can't-proceed ask, and a matched when-rail skipping the router.
    r = h.invoke("run", "wf_router", input='{"n": 1}')
    expect_code(r, "ok", "wf_router strict completes after a router repair")
    state = r.payload["result"]["state"]
    expect("mid" not in state and state["fin"] == {"done": True},
           "wf_router strict must route good->fin (no fall-through): %s" % sorted(state))
    expect(h.count_started("judge#0/route#0") == 1 and h.count_started("judge#0/route#1") == 1,
           "wf_router strict router-repair counts wrong")
    with fresh(h.engine, "wfr-o") as ho:
        ro = ho.invoke("run", "wf_router", input='{"n": 1}', env={"WF_ROUTER": "optional"})
        expect_code(ro, "ok", "wf_router optional completes")
        expect("mid" in ro.payload["result"]["state"],
               "wf_router optional `proceed` must fall onward through mid: %s"
               % sorted(ro.payload["result"]["state"]))
    with fresh(h.engine, "wfr-a") as ha:
        ra = ha.invoke("run", "wf_router", input='{"n": 1}', env={"WF_ROUTER": "ask"})
        expect_code(ra, "suspended", "wf_router ask suspends with the router's reason")
        q = ra.payload["pending"]["question"]["prompt"]
        expect("what budget applies?" in q, "wf_router ask must carry the judge's reason: %r" % q)
        ra2 = ha.invoke("resume", "wf_router", answer='"budget is 10k"', env={"WF_ROUTER": "ask"})
        expect_code(ra2, "ok", "wf_router ask -> feedback -> task re-attempt -> routed")
        expect(ha.count_started("judge#0/llm#1") == 1,
               "wf_router ask must RE-RUN the task with the woven answer")
        expect(ra2.payload["result"]["state"]["judge"] == {"claim": "amended"},
               "wf_router re-attempted task output must win: %s" % ra2.payload["result"]["state"]["judge"])
    with fresh(h.engine, "wfr-c") as hc:
        rc = hc.invoke("run", "wf_router", input='{"n": 1}', env={"WF_ROUTER": "cantproceed"})
        expect_code(rc, "suspended", "wf_router cantproceed forces the reasoned ask")
        q = rc.payload["pending"]["question"]["prompt"]
        expect("could not complete automatically" in q,
               "wf_router forced ask needs the _CANT_PROCEED wording: %r" % q)
    with fresh(h.engine, "wfr-s") as hs:
        rs = hs.invoke("run", "wf_router", input='{"n": 1}', env={"WF_ROUTER": "selfheal"})
        expect_code(rs, "ok", "wf_router selfheal completes")
        expect(hs.count_started("judge#0/llm#0") == 1 and hs.count_started("judge#0/llm#1") == 1,
               "wf_router selfheal must repair the off-menu outcome in one round")
        expect(hs.count_started("judge#0/route#0") == 0,
               "wf_router selfheal must route WITHOUT the judge (fast path after repair)")
    with fresh(h.engine, "wfr-w") as hw:
        rw = hw.invoke("run", "wf_router", input='{"n": 9}', env={"WF_ROUTER": "when_skips"})
        expect_code(rw, "ok", "wf_router when-rail must route without consulting the router")
        expect(hw.count_started("judge#0/route#0") == 0, "wf_router when-rail must SKIP the router")


def wf_onerr_route(h):
    # on_error [{retries, to}]: retry then reroute; the sentinel is auto-stored at $.<state>
    # (forensic truth) while the walk takes the failure branch; object sugar == one-rule list;
    # a retries-only rule still dies (exit 1, with provenance) after its extra attempts.
    with fresh(h.engine, "wfoe-r") as hh:
        r = hh.invoke("run", "wf_onerr", input="{}", env={"WF_ONERR": "route", "BREAK": "1"})
        expect_code(r, "ok", "WFOE route completes via cleanup")
        res = r.payload["result"]
        expect(res["result"] == {"cleaned": True, "why": "bad payload"},
               "WFOE cleanup result wrong: %s" % res["result"])
        expect(res["state"]["fetch"] ==
               {"__error__": {"attempts": 2, "message": "bad payload", "name": "ValueError"}},
               "WFOE $.fetch must hold the sentinel: %s" % res["state"].get("fetch"))
        expect(hh.count_started("fetch#0") == 2, "WFOE retries=1 -> 2 attempts, got %d"
               % hh.count_started("fetch#0"))
        expect(hh.count_started("cleanup#0") == 1, "WFOE cleanup did not run")
        expect(not hh.has_started("use#0/map#0"), "WFOE success path must not run")
    with fresh(h.engine, "wfoe-o") as ho:
        r = ho.invoke("run", "wf_onerr", input="{}", env={"WF_ONERR": "routeobj", "BREAK": "1"})
        expect_code(r, "ok", "WFOE object sugar completes")
        expect(ho.count_started("fetch#0") == 2, "WFOE sugar retries wrong")
    with fresh(h.engine, "wfoe-t") as ht:
        r = ht.invoke("run", "wf_onerr", input="{}", env={"WF_ONERR": "retryonly", "BREAK": "1"})
        expect_code(r, "failed", "WFOE retries-only must still fail")
        expect(ht.count_started("fetch#0") == 3, "WFOE retries=2 -> 3 attempts, got %d"
               % ht.count_started("fetch#0"))
        expect(r.payload["error"].get("step") == "fetch#0",
               "WFOE failure provenance wrong: %s" % r.payload["error"])


def wf_onerr_match(h):
    # first-match-wins ladder: a Timeout matches the retry-only rule (3 attempts then exit 1,
    # never falling through to the catch-all); a ValueError skips it and the match-all rule
    # catches on the FIRST failure and reroutes; a `when` predicate reads the sentinel's name.
    with fresh(h.engine, "wfoe-mt") as hm:
        r = hm.invoke("run", "wf_onerr", input="{}",
                      env={"WF_ONERR": "match", "BREAK": "1", "FAIL_KIND": "timeout"})
        expect_code(r, "failed", "WFOE timeout path dies after its retries")
        expect(hm.count_started("fetch#0") == 3, "WFOE timeout retries wrong: %d"
               % hm.count_started("fetch#0"))
        expect(not hm.has_started("cleanup#0"), "WFOE timeout must NOT fall through to catch-all")
    with fresh(h.engine, "wfoe-mv") as hv:
        r = hv.invoke("run", "wf_onerr", input="{}",
                      env={"WF_ONERR": "match", "BREAK": "1", "FAIL_KIND": "value"})
        expect_code(r, "ok", "WFOE value path catches + `when` reads the sentinel")
        expect(hv.count_started("fetch#0") == 1, "WFOE match-all rule must not retry: %d"
               % hv.count_started("fetch#0"))
        expect(hv.count_started("cleanup#0") == 1, "WFOE cleanup did not run")
    # $-anchor cross-engine parity: a message ending in "\n" must match `timeout$` IDENTICALLY
    # in both engines (trailing newlines are stripped from the match haystack; py `$` would
    # otherwise match before the \n while js `$` would not — a silent branch divergence).
    with fresh(h.engine, "wfoe-nl") as hn:
        r = hn.invoke("run", "wf_onerr", input="{}",
                      env={"WF_ONERR": "matchnl", "BREAK": "1", "FAIL_KIND": "timeoutnl"})
        expect_code(r, "ok", "WFOE trailing-newline message must match `timeout$` (both engines)")
        expect(hn.count_started("cleanup#0") == 1, "WFOE matchnl cleanup did not run")


def wf_onerr_replay(h):
    # THE replay-trap proof: the caught failure is memoized, so fixing the world and resuming
    # KEEPS the failure branch — fetch does not re-run, the walk does not diverge (no exit 3) —
    # and a further re-run is a pure replay.
    env = {"WF_ONERR": "replay"}
    r = h.invoke("run", "wf_onerr", input="{}", env=dict(env, BREAK="1"))
    expect_code(r, "suspended", "WFOE replay suspends at confirm")
    expect(r.payload["pending"]["key"] == "confirm#0",
           "WFOE expected the confirm gate: %s" % r.payload["pending"]["key"])
    expect("bad payload" in r.payload["pending"]["question"]["prompt"],
           "WFOE gate prompt must render the sentinel: %s" % r.payload["pending"]["question"])
    # resume with the environment FIXED (no BREAK): the branch must hold
    r2 = h.invoke("resume", "wf_onerr", answer='"ok"', env=env)
    expect_code(r2, "ok", "WFOE replay resume completes")
    expect(h.count_started("fetch#0") == 1,
           "WFOE fetch re-ran despite the memoized failure branch (%d)" % h.count_started("fetch#0"))
    expect(r2.payload["result"]["state"]["fetch"]["__error__"]["name"] == "ValueError",
           "WFOE sentinel lost on resume: %s" % r2.payload["result"]["state"]["fetch"])
    starts = len(h.records_of("step_started"))
    r3 = h.invoke("run", "wf_onerr", input="{}", env=env)
    expect_code(r3, "ok", "WFOE replay re-run")
    expect(len(h.records_of("step_started")) == starts, "WFOE re-run executed new steps")
    expect(r3.payload["result"] == r2.payload["result"], "WFOE result drift on replay")


def wf_onerr_fallback(h):
    # rule `result` substitution: the journal memoizes the SENTINEL; $.<state> holds the deeply
    # resolved fallback (holes like ${@.__error__.message} interpolate); the downstream map fans
    # out over the substituted empty list; normal routing applies; the whole thing replays stably.
    r = h.invoke("run", "wf_onerr", input="{}", env={"WF_ONERR": "fallback", "BREAK": "1"})
    expect_code(r, "ok", "WFOE fallback completes")
    res = r.payload["result"]
    expect(res["state"]["fetch"] == {"items": [], "why": "bad payload"},
           "WFOE fallback substitution wrong: %s" % res["state"].get("fetch"))
    expect(res["state"]["use"] == [] and res["result"] == [],
           "WFOE empty fallback must fan out zero items: %s" % res)
    completes = [x for x in h.records_of("step_completed") if x.get("key") == "fetch#0"]
    expect(len(completes) == 1 and completes[0].get("result") ==
           {"__error__": {"attempts": 1, "message": "bad payload", "name": "ValueError"}},
           "WFOE journal must hold the sentinel, not the fallback: %s" % completes)
    expect(not h.has_started("use#0/map#0"), "WFOE fallback list must be empty")
    starts = len(h.records_of("step_started"))
    r2 = h.invoke("run", "wf_onerr", input="{}", env={"WF_ONERR": "fallback", "BREAK": "1"})
    expect_code(r2, "ok", "WFOE fallback re-run")
    expect(r2.payload["result"] == r.payload["result"]
           and len(h.records_of("step_started")) == starts,
           "WFOE fallback replay drift")


_MAPERR_INPUT = '{"items": ["a", "bad", "c"]}'
_MAPERR_SENTINEL = {"__error__": {"attempts": 1, "message": "probe failed: bad", "name": "ValueError"}}


def wf_map_itemerr(h):
    # map `on_item_error`: collect keeps the sentinel AT ITS POSITION (reduce sees 3 slots);
    # skip compresses positions (reduce sees 2); the default still kills the whole flow; map-level
    # `retries` apply per item.
    with fresh(h.engine, "wfme-c") as hc:
        r = hc.invoke("run", "wf_map_err", input=_MAPERR_INPUT,
                      env={"WF_MAPERR": "collect", "BREAK": "1"})
        expect_code(r, "ok", "WFME collect completes")
        scan = r.payload["result"]["state"]["scan"]
        expect(scan == {"n": 3, "outs": ["ok:a", _MAPERR_SENTINEL, "ok:c"]},
               "WFME collect shape wrong: %s" % scan)
        expect(hc.count_type("step_failed", "scan#0/map#1") == 1
               and hc.count_type("step_completed", "scan#0/map#1") == 1,
               "WFME item 1 must journal step_failed + a synthesized step_completed")
    with fresh(h.engine, "wfme-s") as hs:
        r = hs.invoke("run", "wf_map_err", input=_MAPERR_INPUT,
                      env={"WF_MAPERR": "skip", "BREAK": "1"})
        expect_code(r, "ok", "WFME skip completes")
        scan = r.payload["result"]["state"]["scan"]
        expect(scan == {"n": 2, "outs": ["ok:a", "ok:c"]}, "WFME skip shape wrong: %s" % scan)
    with fresh(h.engine, "wfme-f") as hf:
        r = hf.invoke("run", "wf_map_err", input=_MAPERR_INPUT,
                      env={"WF_MAPERR": "fail", "BREAK": "1"})
        expect_code(r, "failed", "WFME default must still kill the flow")
        expect(r.payload["error"].get("step") == "scan#0/map#1",
               "WFME failure provenance wrong: %s" % r.payload["error"])
    with fresh(h.engine, "wfme-r") as hr:
        r = hr.invoke("run", "wf_map_err", input=_MAPERR_INPUT,
                      env={"WF_MAPERR": "retry", "BREAK": "1"})
        expect_code(r, "ok", "WFME retry completes")
        expect(hr.count_started("scan#0/map#1") == 2,
               "WFME map retries=1 -> 2 attempts for the bad item, got %d"
               % hr.count_started("scan#0/map#1"))
        expect(hr.count_started("scan#0/map#0") == 1 and hr.count_started("scan#0/map#2") == 1,
               "WFME healthy items must not retry")


def wf_map_itemerr_replay(h):
    # per-item replay-trap: the collected sentinel is memoized; resuming with the environment
    # FIXED keeps the same list (the bad item does NOT re-run).
    env = {"WF_MAPERR": "gate"}
    r = h.invoke("run", "wf_map_err", input=_MAPERR_INPUT, env=dict(env, BREAK="1"))
    expect_code(r, "suspended", "WFMER suspends at confirm")
    r2 = h.invoke("resume", "wf_map_err", answer='"ok"', env=env)
    expect_code(r2, "ok", "WFMER resume completes")
    expect(h.count_started("scan#0/map#1") == 1,
           "WFMER bad item re-ran despite memoized sentinel (%d)" % h.count_started("scan#0/map#1"))
    scan = r2.payload["result"]["state"]["scan"]
    expect(scan == {"n": 3, "outs": ["ok:a", _MAPERR_SENTINEL, "ok:c"]},
           "WFMER list changed across resume: %s" % scan)


def wf_nonidem(h):
    # `"idempotent": false` on a run state wires the in-doubt machinery: a mid-step crash leaves a
    # dangling start -> exit 11 (options = the resolve verbs) -> resume --resolve completed applies
    # the human's value WITHOUT re-running; --resolve retry re-executes ONCE with the SAME
    # idempotency key, so the keyed side effect still lands exactly once.
    ledger = os.path.join(h.state_dir, "ledger.txt")
    os.makedirs(h.state_dir, exist_ok=True)
    r0 = h.invoke("run", "wf_nonidem", input="null", env={"LEDGER": ledger, "CRASH": "1"})
    expect(r0.code == 137, "WFNI expected a hard crash (137), got %d" % r0.code)
    r = h.invoke("run", "wf_nonidem", input="null", env={"LEDGER": ledger})
    expect_code(r, "in_doubt", "WFNI dangling non-idempotent step must escalate")
    p = r.payload["pending"]
    expect(p["key"] == "pay#0" and p["options"] == ["completed", "retry", "abort"],
           "WFNI pending wrong: %s" % p)
    expect(h.count_started("pay#0") == 1, "WFNI must not blind re-run")
    r2 = h.invoke("resume", "wf_nonidem", resolve="completed",
                  resolve_value='{"paid": true, "manual": true}', env={"LEDGER": ledger})
    expect_code(r2, "ok", "WFNI resolve completed")
    expect(r2.payload["result"]["state"]["pay"] == {"paid": True, "manual": True},
           "WFNI resolved value must land at $.pay: %s" % r2.payload["result"]["state"]["pay"])
    expect(h.count_started("pay#0") == 1, "WFNI resolve completed must not re-run")
    with open(ledger, encoding="utf-8") as f:
        expect(len([x for x in f.read().split("\n") if x]) == 1,
               "WFNI side effect must have landed exactly once")
    # --resolve retry: re-execute once, same idempotency key -> the ledger still has ONE line
    with fresh(h.engine, "wfni-r") as hr:
        ledger2 = os.path.join(hr.state_dir, "ledger.txt")
        os.makedirs(hr.state_dir, exist_ok=True)
        r0 = hr.invoke("run", "wf_nonidem", input="null", env={"LEDGER": ledger2, "CRASH": "1"})
        expect(r0.code == 137, "WFNI retry-case crash expected")
        expect_code(hr.invoke("run", "wf_nonidem", input="null", env={"LEDGER": ledger2}),
                    "in_doubt", "WFNI retry-case in_doubt")
        r3 = hr.invoke("resume", "wf_nonidem", resolve="retry", env={"LEDGER": ledger2})
        expect_code(r3, "ok", "WFNI resolve retry completes")
        expect(hr.count_started("pay#0") == 2, "WFNI retry must re-execute once (%d)"
               % hr.count_started("pay#0"))
        pay = r3.payload["result"]["state"]["pay"]
        expect(pay["paid"] is True and pay["idem"].endswith(":pay#0"),
               "WFNI fn must receive the idem_key: %s" % pay)
        with open(ledger2, encoding="utf-8") as f:
            expect(len([x for x in f.read().split("\n") if x]) == 1,
                   "WFNI idem_key dedupe failed across the crash-window retry")


# --------------------------------------------------------------- nested ctx.call (code-first, CLI/FileStore)
def call_wf_child(h):
    # THE LAYER-COMPOSITION PIN: a workflow-spec flow as a ctx.call child. Two nested suspensions
    # (worker ASK, then the ask gate); resume re-walks the embedded child journal — the memoized
    # model call is never re-invoked, the gate is asked once, the continuation convo is APPENDED
    # (never re-prompted), pre-pause state renders post-pause, and the child's {result, state}
    # memoizes into the parent exactly once.
    r = h.invoke("run", "call_wf_child", input='"req-1"')
    expect_code(r, "suspended", "call_wf_child run")
    pend = r.payload["pending"]
    expect(pend["key"] == "vet/assess#0/intervene#0"
           and pend["chain"] == ["vet", "assess#0/intervene#0"]
           and pend["question"]["prompt"] == "need clearance level",
           "call_wf_child hoisted pending wrong: %s" % pend)
    r2 = h.invoke("resume", "call_wf_child", answer='"clearance A"')
    expect_code(r2, "suspended", "call_wf_child resume-1 reaches the ask gate")
    pend2 = r2.payload["pending"]
    expect(pend2["key"] == "vet/review#0" and "T-909" in pend2["question"]["prompt"],
           "call_wf_child gate must render PRE-pause state POST-pause: %s" % pend2)
    # no-reissue pins, read from the LATEST embedded child journal snapshot
    calls = [c for c in h.records_of("call_suspended") if c["key"] == "vet"]
    expect(len(calls) == 2, "call_wf_child expects two embedded suspensions, got %d" % len(calls))
    crecs = calls[-1]["child_state"]["records"]
    def _n(t, k):
        return sum(1 for x in crecs if x.get("type") == t and x.get("key") == k)
    expect(_n("step_started", "assess#0/llm#0") == 1,
           "call_wf_child: the interrupted model call was RE-INVOKED in the child")
    expect(_n("step_started", "assess#0/llm#1") == 1 and _n("ask_requested", "assess#0/intervene#0") == 1,
           "call_wf_child: continuation/gate counts wrong in the embedded journal")
    r3 = h.invoke("resume", "call_wf_child", answer='"yes"')
    expect_code(r3, "ok", "call_wf_child resume-2 completes")
    vet = r3.payload["result"]["vet"]
    expect(vet["result"] == {"done": True, "token_seen": "T-909", "verdict": "good"},
           "call_wf_child child result wrong: %s" % vet["result"])
    expect(vet["state"]["verdict"] == "good" and vet["state"]["prep"] == {"token": "T-909"},
           "call_wf_child child global state lost: %s" % sorted(vet["state"]))
    assess = vet["state"]["assess"]
    expect(assess["echo_roles"] == ["system", "user", "assistant", "user"]
           and assess["echo_prev"].startswith("ASK:")
           and assess["echo_last"] == "The human answered: clearance A. "
                                      "Continue the instruction with this information.",
           "call_wf_child continuation convo must be APPENDED, not re-prompted: %s" % assess)
    completed = [x for x in h.records_of("step_completed") if x["key"] == "vet"]
    expect(len(completed) == 1, "call_wf_child child must memoize as ONE step_completed")
    starts = len(h.records_of("step_started"))
    r4 = h.invoke("run", "call_wf_child", input='"req-1"')
    expect_code(r4, "ok", "call_wf_child re-run")
    expect(len(h.records_of("step_started")) == starts, "call_wf_child re-ran a memoized step")


def call_cli_2level(h):
    # a ctx.call child suspending bubbles up as the PARENT's own suspend, hoisted+namespaced;
    # resume threads the answer down and memoizes the child's result as an ordinary step.
    r = h.invoke("run", "call_top_2level", input="null")
    expect_code(r, "suspended", "call_cli_2level run")
    expect(r.payload["pending"]["key"] == "child/gate" and r.payload["pending"]["chain"] == ["child", "gate"],
           "call_cli_2level pending wrong: %s" % r.payload["pending"])
    calls = h.records_of("call_suspended")
    expect(len(calls) == 1 and calls[0]["key"] == "child"
           and any(x.get("type") == "ask_requested" for x in calls[0]["child_state"]["records"]),
           "call_cli_2level must journal ONE call_suspended embedding the leaf's sub-journal: %s" % calls)
    r2 = h.invoke("resume", "call_top_2level", answer='"ok"')
    expect_code(r2, "ok", "call_cli_2level resume")
    expect(r2.payload["result"] == {"from_child": {"leaf_ans": "ok"}}, "call_cli_2level result: %s" % r2.payload)
    completed = [x for x in h.records_of("step_completed") if x["key"] == "child"]
    expect(completed, "call_cli_2level child must memoize as an ordinary step_completed")
    expect(not h.records_of("call_suspended")[-1:] or
           len([c for c in h.records_of("call_suspended") if c["key"] == "child"]) == 1,
           "call_cli_2level: no residual re-suspension")


def call_cli_3level(h):
    # depth CHANGES between resumes: first suspend at depth 3 (child/leaf/gate), second at depth
    # 2 (child/mid_gate) — proves the resume mechanism doesn't assume a fixed chain shape.
    r = h.invoke("run", "call_top_3level", input="null")
    expect_code(r, "suspended", "call_cli_3level first suspend")
    expect(r.payload["pending"]["key"] == "child/leaf/gate", "call_cli_3level depth-3: %s" % r.payload["pending"])
    r2 = h.invoke("resume", "call_top_3level", answer='"ok"')
    expect_code(r2, "suspended", "call_cli_3level second suspend")
    expect(r2.payload["pending"]["key"] == "child/mid_gate", "call_cli_3level depth-2: %s" % r2.payload["pending"])
    r3 = h.invoke("resume", "call_top_3level", answer='"ok"')
    expect_code(r3, "ok", "call_cli_3level completes")
    expect(r3.payload["result"] == {"from_child": {"from_leaf": {"leaf_ans": "ok"}, "mid_ans": "ok"}},
           "call_cli_3level result: %s" % r3.payload)


def call_crashboundary(h):
    # ACCEPTED trade-off, demonstrated empirically: a crash inside a ctx.call child leaves NO
    # durable record at all (Context.call hasn't unwound to append call_suspended yet) — unlike
    # an equivalent TOP-LEVEL non-idempotent step under FileStore (contrast wf_nonidem/l08n,
    # which correctly escalate to in-doubt instead of silently re-firing).
    ledger = os.path.join(h.state_dir, "ledger.txt")
    os.makedirs(h.state_dir, exist_ok=True)
    r0 = h.invoke("run", "call_parent_crashboundary", input="null",
                 env={"CALL_LEDGER": ledger, "CALL_CRASH": "1"})
    expect(r0.code == 137, "call_crashboundary expected a hard crash (137), got %d" % r0.code)
    expect(not h.records_of("call_suspended"),
           "call_crashboundary: no call_suspended should be journaled — the crash happened "
           "before Context.call could unwind (%s)" % h.records_of("call_suspended"))
    r1 = h.invoke("run", "call_parent_crashboundary", input="null", env={"CALL_LEDGER": ledger})
    expect_code(r1, "ok", "call_crashboundary clean re-run")
    with open(ledger, encoding="utf-8") as f:
        lines = [x for x in f.read().split("\n") if x]
    expect(len(lines) == 2,
           "call_crashboundary: the non-idempotent side effect must have fired TWICE with no "
           "in-doubt escalation (ledger=%s) — this is the accepted cost of a ctx.call child "
           "always being MemoryStore-backed" % lines)


def call_collision(h):
    # ctx.call funnels through the ordinary _request collision guard — no new logic needed.
    r = h.invoke("run", "call_collision", input="null")
    expect_code(r, "usage", "call_collision must raise KeyCollision")
    expect("duplicate step/ask key in one pass" in r.raw, "call_collision stderr unclear: %s" % r.raw)


def call_memo_strict_gap(h):
    # Pins the Memo._build fix: an in-flight, still-OPEN call_suspended's key must join
    # key_order immediately (like ask_requested already does) — renaming it before resolution
    # must raise NonDeterminism (exit 3), not silently pass strict-replay.
    h.seed_journal([
        {"type": "run_started", "run_id": "R", "flow_id": "top_2level", "flow_version": 1,
         "engine": h.engine, "input": None},
        {"type": "call_suspended", "key": "renamed_child", "child_state": {
            "v": 1, "engine": "py", "version": 2,
            "records": [
                {"v": 1, "type": "run_started", "run_id": "C", "flow_id": "leaf",
                 "flow_version": 1, "engine": "py", "input": None},
                {"v": 1, "type": "ask_requested", "key": "gate",
                 "question": {"prompt": "leaf gate?", "options": ["ok"]}, "schema": None},
            ],
            "blobs": {},
        }},
    ])
    r = h.invoke("run", "call_top_2level", input="null")
    expect_code(r, "skew", "call_memo_strict_gap must catch the renamed open call key")
    expect("NonDeterminism" in r.raw or "replay divergence" in r.raw,
           "call_memo_strict_gap stderr unclear: %s" % r.raw)


def call_statejson(h):
    # state.json (the derived status pointer) for a CLI parent suspended on a NESTED call must
    # carry the hoisted key + chain and mirror the stdout payload exactly, with the PARENT's
    # run_id (the child's own run_id lives only inside the embedded child_state).
    r = h.invoke("run", "call_top_2level", input="null")
    expect_code(r, "suspended", "call_statejson setup")
    with open(os.path.join(h.state_dir, "state.json"), encoding="utf-8") as f:
        state = json.load(f)
    expect(state["status"] == "suspended", "call_statejson status: %s" % state["status"])
    expect(state["pending"]["key"] == "child/gate" and state["pending"]["chain"] == ["child", "gate"],
           "call_statejson pending must be hoisted: %s" % state["pending"])
    expect(state["pending"] == r.payload["pending"],
           "call_statejson: state.json must mirror the stdout payload exactly:\n  %s\n  %s"
           % (state["pending"], r.payload["pending"]))
    run_ids = {x["run_id"] for x in h.records_of("run_started")}
    expect(state["run_id"] in run_ids and len(run_ids) == 1,
           "call_statejson: state.json run_id must be the PARENT journal's: %s vs %s"
           % (state["run_id"], run_ids))


def call_auto_nested(h):
    # CLI --auto over a ctx.call flow: the child's unanswerable gate must (a) exit 12 with ONE
    # needs_answer payload carrying the HOISTED key + chain (emitted at the top level via
    # _derive_status, not from auto_answer deep inside the child), and (b) leave the child's
    # progress durably embedded (call_suspended) so a later non-auto resume just works.
    r = h.invoke("run", "call_top_2level", input="null", auto=True)
    expect(r.code == 12, "call_auto_nested expected exit 12, got %d (%s)" % (r.code, r.raw))
    expect(r.payload["status"] == "needs_answer", "call_auto_nested payload: %s" % r.payload)
    expect(r.payload["pending"]["key"] == "child/gate"
           and r.payload["pending"]["chain"] == ["child", "gate"]
           and r.payload["pending"]["question"] is not None,
           "call_auto_nested pending must be the hoisted shape: %s" % r.payload["pending"])
    calls = h.records_of("call_suspended")
    expect(len(calls) == 1 and calls[0]["key"] == "child"
           and any(x.get("type") == "ask_requested" for x in calls[0]["child_state"]["records"]),
           "call_auto_nested: child state must be durably embedded despite the SystemExit path: %s"
           % [c.get("key") for c in calls])
    r2 = h.invoke("resume", "call_top_2level", answer='"ok"')
    expect_code(r2, "ok", "call_auto_nested non-auto resume")
    expect(r2.payload["result"] == {"from_child": {"leaf_ans": "ok"}},
           "call_auto_nested result: %s" % r2.payload)


def call_key_target(h):
    # Path-aware key addressing via the CLI: the hoisted pending.key round-trips verbatim as
    # --key, the bare leaf-local form keeps working, and a wrong key is a clean exit-2 payload
    # that consumes NOTHING (only observable against a durable on-disk journal — hence CLI rung).
    with fresh(h.engine, "key-hoisted") as ha:
        r = ha.invoke("run", "call_top_2level", input="null")
        expect_code(r, "suspended", "call_key_target setup")
        r2 = ha.invoke("resume", "call_top_2level", answer='"ok"', key="child/gate")
        expect_code(r2, "ok", "call_key_target hoisted --key child/gate")
        expect(r2.payload["result"] == {"from_child": {"leaf_ans": "ok"}},
               "call_key_target hoisted result: %s" % r2.payload)
    with fresh(h.engine, "key-leaf") as hb:
        hb.invoke("run", "call_top_2level", input="null")
        r3 = hb.invoke("resume", "call_top_2level", answer='"ok"', key="gate")
        expect_code(r3, "ok", "call_key_target leaf-local --key gate")
    with fresh(h.engine, "key-wrong") as hc:
        hc.invoke("run", "call_top_2level", input="null")
        r4 = hc.invoke("resume", "call_top_2level", answer='"ok"', key="nope")
        expect(r4.code == 2, "call_key_target wrong key must exit 2, got %d" % r4.code)
        expect(r4.payload is not None and r4.payload["status"] == "error" and "nope" in r4.payload["error"],
               "call_key_target wrong key needs a clean error payload: %s" % r4.raw)
        expect("Traceback" not in r4.raw, "call_key_target wrong key must not traceback: %s" % r4.raw)
        answered = [x for x in hc.records_of("ask_answered")]
        expect(not answered, "call_key_target: rejection must consume NOTHING (found %s)" % answered)
        r5 = hc.invoke("resume", "call_top_2level", answer='"ok"')
        expect_code(r5, "ok", "call_key_target keyless resume after rejection")
    with fresh(h.engine, "key-deep") as hd:
        hd.invoke("run", "call_top_3level", input="null")
        r6 = hd.invoke("resume", "call_top_3level", answer='"ok"', key="child/leaf/gate")
        expect_code(r6, "suspended", "call_key_target deep key")
        expect(r6.payload["pending"]["key"] == "child/mid_gate",
               "call_key_target: deep answer must land at depth 3, next suspend at depth 2: %s"
               % r6.payload["pending"])


# --------------------------------------------------------------- investigation tier (inv_*)
# Drive examples/investigate_repo (via tests/ladder/inv_repo) with a fixture backend, so the
# ladder covers the same durable "triage a failing test" flow that runs for real in Phase 3.
INV_FIX_DIR = os.path.join(HERE, "fixtures", "investigation")


def _inv_env(fixture="tools.json", **extra):
    e = {"INVESTIGATE_MODE": "fixture",
         "INVESTIGATE_FIXTURE": os.path.join(INV_FIX_DIR, fixture)}
    e.update(extra)
    return e


def inv_fix_resume(h):
    # CENTERPIECE: a broken environment fails `reproduce`; fix it out of band and resume
    # re-runs ONLY that step — the expensive scan stays memoized. command-fails->fix->resume.
    r = h.invoke("run", "inv_repo", input='{"goal":"test_verify"}',
                 env=_inv_env(INVESTIGATE_DEP_DOWN="1"))
    expect_code(r, "failed", "INV fix_resume: broken env fails reproduce")
    r2 = h.invoke("run", "inv_repo", input='{"goal":"test_verify"}', env=_inv_env())
    expect_code(r2, "suspended", "INV fix_resume: recovered to a gate")
    expect(r2.payload["pending"]["key"] == "focus", "INV fix_resume: at focus gate: %s" % r2.payload)
    expect(h.count_started("map") == 1,
           "INV fix_resume: map scanned once, not re-scanned (%d)" % h.count_started("map"))
    expect(h.count_started("reproduce") == 2,
           "INV fix_resume: reproduce re-attempted after the fix (%d)" % h.count_started("reproduce"))


def inv_map_memo(h):
    # the expensive scan is memoized across a resume.
    r = h.invoke("run", "inv_repo", input='{"goal":"g"}', env=_inv_env())
    expect_code(r, "suspended", "INV map_memo: run to focus gate")
    expect(h.count_started("map") == 1, "INV map_memo: scanned once on run")
    r2 = h.invoke("resume", "inv_repo", answer='"src/auth.py"', env=_inv_env())
    expect_code(r2, "suspended", "INV map_memo: resume to approve gate")
    expect(r2.payload["pending"]["key"] == "approve-fix", "INV map_memo: at approve gate: %s" % r2.payload)
    expect(h.count_started("map") == 1,
           "INV map_memo: still scanned once after resume (%d)" % h.count_started("map"))


def inv_focus_gate(h):
    # the focus gate is typed to the located suspects; off-enum bounces; the choice is inspected.
    r = h.invoke("run", "inv_repo", input='{"goal":"g"}', env=_inv_env())
    expect_code(r, "suspended", "INV focus: run to focus gate")
    expect(r.payload["pending"]["key"] == "focus", "INV focus: at focus gate")
    expect(r.payload["pending"]["schema"]["enum"] == ["src/auth.py", "tests/test_auth.py"],
           "INV focus: enum should be the located suspects: %s" % r.payload["pending"].get("schema"))
    bad = h.invoke("resume", "inv_repo", answer='"nope.py"', env=_inv_env())
    expect_code(bad, "usage", "INV focus: off-enum answer rejected, gate stays open")
    r2 = h.invoke("resume", "inv_repo", answer='"src/auth.py"', env=_inv_env())
    expect_code(r2, "suspended", "INV focus: valid choice -> approve gate")
    expect(r2.payload["pending"]["key"] == "approve-fix" and h.count_started("inspect") == 1,
           "INV focus: inspected the choice, now at approve gate: %s" % r2.payload)


def inv_apply_crash(h):
    # the mutating step is killed mid-edit -> in-doubt -> resolve completed -> verify.
    expect_code(h.invoke("run", "inv_repo", input='{"goal":"g"}', env=_inv_env()),
                "suspended", "INV apply_crash: run to focus")
    expect_code(h.invoke("resume", "inv_repo", answer='"src/auth.py"', env=_inv_env()),
                "suspended", "INV apply_crash: to approve gate")
    crash = h.invoke("resume", "inv_repo", answer="true", env=_inv_env(INVESTIGATE_CRASH_APPLY="1"))
    expect(crash.code == 137, "INV apply_crash: killed mid-edit (exit %d)" % crash.code)
    r = h.invoke("run", "inv_repo", input='{"goal":"g"}', env=_inv_env())
    expect_code(r, "in_doubt", "INV apply_crash: escalates to in-doubt")
    expect(r.payload["pending"]["key"] == "apply-fix", "INV apply_crash: apply-fix in doubt: %s" % r.payload)
    done = h.invoke("resume", "inv_repo", resolve="completed",
                    resolve_value='{"patched":"src/auth.py"}', env=_inv_env())
    expect_code(done, "ok", "INV apply_crash: resolved + verified")
    expect(done.payload["result"]["status"] == "fixed", "INV apply_crash: fixed: %s" % done.payload)
    expect(h.count_started("apply-fix") == 1,
           "INV apply_crash: apply-fix ran exactly once (%d)" % h.count_started("apply-fix"))


def inv_report_branch(h):
    # declining the fix -> report-only; the mutating step never runs.
    expect_code(h.invoke("run", "inv_repo", input='{"goal":"g"}', env=_inv_env()),
                "suspended", "INV report: run to focus")
    expect_code(h.invoke("resume", "inv_repo", answer='"src/auth.py"', env=_inv_env()),
                "suspended", "INV report: to approve gate")
    r = h.invoke("resume", "inv_repo", answer="false", env=_inv_env())
    expect_code(r, "ok", "INV report: declined -> completed")
    expect(r.payload["result"]["status"] == "reported" and r.payload["result"]["fix_applied"] is False,
           "INV report: reported without a fix: %s" % r.payload)
    expect(h.count_started("apply-fix") == 0, "INV report: apply-fix never ran")


def inv_full(h):
    # happy path end to end.
    expect_code(h.invoke("run", "inv_repo", input='{"goal":"g"}', env=_inv_env()), "suspended", "INV full: focus")
    expect_code(h.invoke("resume", "inv_repo", answer='"src/auth.py"', env=_inv_env()), "suspended", "INV full: approve")
    r = h.invoke("resume", "inv_repo", answer="true", env=_inv_env())
    expect_code(r, "ok", "INV full: completed")
    res = r.payload["result"]
    expect(res["status"] == "fixed" and res["suspect"] == "src/auth.py" and res["fix_applied"] is True,
           "INV full: fixed result: %s" % res)


def inv_flaky_retry(h):
    # a flaky failure: bounded retries, then a human proceed/abandon decision.
    r = h.invoke("run", "inv_repo", input='{"goal":"g"}', env=_inv_env("flaky.json"))
    expect_code(r, "suspended", "INV flaky: run to flaky-decision")
    expect(r.payload["pending"]["key"] == "flaky-decision", "INV flaky: at flaky gate: %s" % r.payload)
    expect(h.count_started("reproduce") == 1 and h.count_started("reproduce-retry#0") == 1
           and h.count_started("reproduce-retry#1") == 1,
           "INV flaky: reproduce retried MAX_FLAKY times before escalating")
    r2 = h.invoke("resume", "inv_repo", answer='"proceed"', env=_inv_env("flaky.json"))
    expect_code(r2, "suspended", "INV flaky: proceed -> focus gate")
    expect(r2.payload["pending"]["key"] == "focus", "INV flaky: proceed reaches focus: %s" % r2.payload)
    with fresh(h.engine, "inv_flaky_abandon") as hb:
        expect_code(hb.invoke("run", "inv_repo", input='{"goal":"g"}', env=_inv_env("flaky.json")),
                    "suspended", "INV flaky-abandon: run")
        ra = hb.invoke("resume", "inv_repo", answer='"abandon"', env=_inv_env("flaky.json"))
        expect_code(ra, "ok", "INV flaky-abandon: completed")
        expect(ra.payload["result"]["status"] == "abandoned-flaky",
               "INV flaky-abandon: status: %s" % ra.payload)


RUNGS = [
    ("l00", l00), ("l01", l01), ("l02", l02), ("l03", l03), ("l04", l04), ("l05", l05),
    ("l06", l06), ("l07", l07), ("l08", l08), ("l09", l09), ("l10", l10), ("l11", l11),
    ("l12", l12), ("l13_onfail", l13_onfail),
    ("lprop", lprop), ("lvalues", lvalues), ("lauto", lauto), ("lidem", lidem),
    ("lhelpers", lhelpers), ("lfailmeta", lfailmeta),
    ("loutfile", loutfile), ("loutfile_badpath", loutfile_badpath),
    ("e1", e1), ("e2", e2), ("e3", e3), ("e4", e4), ("e5", e5), ("e6", e6), ("lstate", lstate),
    ("g1", g1), ("g2", g2), ("g3", g3), ("g4", g4), ("g5", g5),
    ("d1", d1), ("d2", d2), ("d3", d3), ("d4", d4), ("d5", d5),
    ("r1", r1), ("r2", r2), ("r3", r3), ("r4", r4), ("r5", r5), ("r6", r6),
    ("inresolve", inresolve), ("inoptions", inoptions),
    ("obs", obs),
    ("wf_state", wf_state), ("wf_route", wf_route), ("wf_return", wf_return),
    ("wf_context", wf_context),
    ("wf_decide", wf_decide), ("wf_abort", wf_abort),
    ("wf_intervene", wf_intervene),
    ("wf_intervene_multi", wf_intervene_multi),
    ("wf_paths", wf_paths), ("wf_search", wf_search), ("wf_map", wf_map),
    ("wf_mapsusp", wf_mapsusp), ("wf_mapedge", wf_mapedge), ("wf_mapbad", wf_mapbad),
    ("wf_specbad", wf_specbad), ("wf_seq", wf_seq), ("wf_cycle", wf_cycle), ("wf_scaffold", wf_scaffold), ("wf_router", wf_router), ("call_wf_child", call_wf_child),
    ("l_inhash", l_inhash), ("wf_rehash", wf_rehash),
    ("wf_onerr_route", wf_onerr_route), ("wf_onerr_match", wf_onerr_match),
    ("wf_onerr_replay", wf_onerr_replay), ("wf_onerr_fallback", wf_onerr_fallback),
    ("wf_map_itemerr", wf_map_itemerr), ("wf_map_itemerr_replay", wf_map_itemerr_replay),
    ("wf_nonidem", wf_nonidem),
    ("call_cli_2level", call_cli_2level), ("call_cli_3level", call_cli_3level),
    ("call_crashboundary", call_crashboundary), ("call_collision", call_collision),
    ("call_memo_strict_gap", call_memo_strict_gap), ("call_key_target", call_key_target),
    ("call_auto_nested", call_auto_nested), ("call_statejson", call_statejson),
    ("inv_fix_resume", inv_fix_resume), ("inv_map_memo", inv_map_memo),
    ("inv_focus_gate", inv_focus_gate), ("inv_apply_crash", inv_apply_crash),
    ("inv_report_branch", inv_report_branch), ("inv_full", inv_full),
    ("inv_flaky_retry", inv_flaky_retry),
]


def _all_rungs_by_name():
    """Union of THIS ladder's rungs and run_call_ladder's (the library-API ladder) — suites may
    reference either. Call-ladder rungs take and ignore the harness arg (`(_h=None)`), so the
    run loop below drives both uniformly. The two name sets must stay disjoint."""
    import run_call_ladder
    mine = dict(RUNGS)
    theirs = dict(run_call_ladder.RUNGS)
    overlap = set(mine) & set(theirs)
    if overlap:
        raise SystemExit("rung name(s) defined in BOTH ladders: %s" % sorted(overlap))
    mine.update(theirs)
    return mine


def _validate_suites():
    """Every name in every suite must be a real rung in EITHER ladder, and — the reverse — every
    rung in either ladder must belong to at least one suite. Fail loud on drift in BOTH
    directions (rename/removal → unknown name; brand-new rung never indexed → orphan)."""
    from suites import SUITES
    names = set(_all_rungs_by_name())
    covered = set()
    for suite, members in SUITES.items():
        unknown = [m for m in members if m not in names]
        if unknown:
            raise SystemExit("suite %r references unknown rung(s): %s" % (suite, unknown))
        covered.update(members)
    orphans = sorted(names - covered)
    if orphans:
        raise SystemExit("rung(s) not in ANY suite: %s — add each to a suite in tests/suites.py"
                         % orphans)
    return SUITES


# --------------------------------------------------------------------------- main
def main(argv=None):
    global _EV, _EV_CHECKS
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="py", choices=["py"])  # js mirror quarantined to extras/js-mirror/
    ap.add_argument("-k", default=None, help="only rungs whose name contains this")
    ap.add_argument("--through", default=None, help="stop after this rung name")
    ap.add_argument("--suite", default=None,
                    help="run only the named suite (see tests/suites.py); combine with -k to "
                         "further substring-filter within it")
    ap.add_argument("--list-suites", action="store_true",
                    help="list suites + rung counts, validate every name against RUNGS, exit")
    ap.add_argument("--evidence", action="store_true",
                    help="show each rung's real receipts: the run/resume calls it made, "
                         "the engine's exit code/status/pending, and invariants verified")
    args = ap.parse_args(argv)

    suites = _validate_suites()
    if args.list_suites:
        for name, members in suites.items():
            print("%-16s %3d rungs: %s" % (name, len(members), ", ".join(members)))
        return 0

    rungs = RUNGS
    if args.suite:
        if args.suite not in suites:
            print("unknown suite %r; available: %s" % (args.suite, ", ".join(sorted(suites))))
            return 2
        by_name = _all_rungs_by_name()
        rungs = [(n, by_name[n]) for n in suites[args.suite]]

    passed = []
    total_calls = 0
    for name, fn in rungs:
        if args.k and args.k not in name:
            continue
        tmp = tempfile.mkdtemp(prefix="ladder-%s-" % name)
        h = Harness(args.engine, tmp)
        if args.evidence:
            _EV, _EV_CHECKS = [], 0
        try:
            fn(h)
            passed.append(name)
            if args.evidence:
                total_calls += len(_EV)
                print("\n  ── %s ── (%d call(s), %d invariant(s) verified)"
                      % (name, len(_EV), _EV_CHECKS))
                for ln in _EV:
                    print(ln)
            else:
                print("  PASS %s" % name)
        except AssertionError as e:
            # AssertionError, not our LadderError: suites may dispatch run_call_ladder rungs,
            # whose own LadderError is a sibling AssertionError subclass, not ours.
            print("\nFAIL at rung %s (engine=%s)\n  %s" % (name, args.engine, e))
            if args.evidence and _EV:
                print("  --- calls before failure ---")
                for ln in _EV:
                    print(ln)
            print("  --- journal (%s) ---" % h.state_dir)
            for rec in h.journal():
                print("    " + json.dumps(rec, sort_keys=True))
            return 1
        finally:
            _EV = None
            shutil.rmtree(tmp, ignore_errors=True)
        if args.through and name == args.through:
            break
    if args.evidence:
        print("\nLadder OK [%s]: %d rungs, %d engine call(s) shown above."
              % (args.engine, len(passed), total_calls))
    else:
        print("\nLadder OK [%s]: %s" % (args.engine, ", ".join(passed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
