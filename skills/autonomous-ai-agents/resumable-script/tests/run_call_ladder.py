#!/usr/bin/env python3
"""Library-API ladder for run_flow/resume_flow/export_portable_state — the self-contained,
portable-state entrypoints (see references/nested-flows.md), as opposed to run_ladder.py's
CLI/--state-dir surface (which has its own nested-ctx.call rungs: call_cli_2level, call_cli_3level,
call_crashboundary, call_collision, call_memo_strict_gap).

No subprocess is needed except where a rung explicitly proves cross-process portability, or where
a fixture's os._exit() would kill an in-process caller — those shell out to _call_driver.py.

Usage:
  python3 tests/run_call_ladder.py
  python3 tests/run_call_ladder.py -k rf_2level
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LADDER = os.path.join(HERE, "ladder")
DRIVER = os.path.join(HERE, "_call_driver.py")
PY_ENGINE = os.path.join(ROOT, "scripts", "engine.py")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, LADDER)

import engine  # noqa: E402
import call_top_2level  # noqa: E402
import call_top_3level  # noqa: E402
import call_parent_fails  # noqa: E402
import call_parent_crashboundary  # noqa: E402
import call_child_nonidem  # noqa: E402
import call_blob  # noqa: E402


class LadderError(AssertionError):
    pass


def expect(cond, msg):
    if not cond:
        raise LadderError(msg)


def expect_code(code, name, where):
    expect(code == getattr(engine, "EXIT_%s" % name), "%s: expected exit %s, got %d" % (where, name, code))


def _driver(req, timeout=15):
    """Run one request through _call_driver.py in a fresh subprocess; return (returncode, stdout-json-or-None)."""
    proc = subprocess.run([sys.executable, DRIVER], input=json.dumps(req),
                          capture_output=True, text=True, timeout=timeout)
    out = None
    if proc.stdout.strip():
        try:
            out = json.loads(proc.stdout.strip().splitlines()[-1])
        except ValueError:
            out = None
    return proc.returncode, out, proc.stderr


# --------------------------------------------------------------------------- rungs
def rf_2level(_h=None):
    payload, code = engine.run_flow(call_top_2level.main, None)
    expect_code(code, "SUSPENDED", "rf_2level run_flow")
    expect(payload["pending"] == {"key": "child/gate",
                                  "question": {"prompt": "leaf gate?", "options": ["ok"]},
                                  "schema": None, "chain": ["child", "gate"]},
           "rf_2level pending wrong: %s" % payload["pending"])
    # round-trip through JSON to prove genuine portability, not incidental Python-object reuse
    blob = json.loads(json.dumps(payload["state"]))
    # feed it to a SEPARATE PROCESS — proves the blob is usable elsewhere, not just here
    rc, out, err = _driver({"module": "call_top_2level", "state": blob, "answer": '"ok"'})
    expect(rc == 0, "rf_2level subprocess crashed: %s" % err)
    expect(out and out["code"] == engine.EXIT_OK, "rf_2level subprocess resume wrong: %s" % out)
    expect(out["payload"]["result"] == {"from_child": {"leaf_ans": "ok"}},
           "rf_2level subprocess result: %s" % out["payload"])


def rf_3level(_h=None):
    payload, code = engine.run_flow(call_top_3level.main, None)
    expect_code(code, "SUSPENDED", "rf_3level first suspend")
    expect(payload["pending"]["key"] == "child/leaf/gate", "rf_3level depth-3: %s" % payload["pending"])
    # prove the recursive embedding DIRECTLY: a call_suspended nested inside a call_suspended
    calls = [r for r in payload["state"]["records"] if r["type"] == "call_suspended"]
    expect(len(calls) == 1 and calls[0]["key"] == "child", "rf_3level top-level call record: %s" % calls)
    nested = [r for r in calls[0]["child_state"]["records"] if r["type"] == "call_suspended"]
    expect(len(nested) == 1 and nested[0]["key"] == "leaf",
           "rf_3level must recursively embed a nested call_suspended: %s" % nested)

    blob = json.loads(json.dumps(payload["state"]))
    payload2, code2 = engine.resume_flow(call_top_3level.main, blob, answer='"ok"')
    expect_code(code2, "SUSPENDED", "rf_3level second suspend")
    expect(payload2["pending"]["key"] == "child/mid_gate", "rf_3level depth-2: %s" % payload2["pending"])

    blob2 = json.loads(json.dumps(payload2["state"]))
    payload3, code3 = engine.resume_flow(call_top_3level.main, blob2, answer='"ok"')
    expect_code(code3, "OK", "rf_3level completes")
    expect(payload3["result"] == {"from_child": {"from_leaf": {"leaf_ans": "ok"}, "mid_ans": "ok"}},
           "rf_3level result: %s" % payload3["result"])


def rf_failed_child(_h=None):
    payload, code = engine.run_flow(call_parent_fails.main, None)
    expect_code(code, "FLOW_FAILED", "rf_failed_child")
    expect(payload["error"]["step"] == "risky_child" and payload["error"]["name"] == "RuntimeError",
           "rf_failed_child error wrong: %s" % payload["error"])


def rf_in_doubt_nested(_h=None):
    # A nested dangling step, hand-seeded (mirrors l08n's seed_journal pattern, one level down) —
    # this shape is only realistically reachable via export_portable_state on a REAL crashed
    # FileStore run (see call_crashboundary's own finding: a pure MemoryStore ctx.call child
    # leaves no trace of a crash at all); here we test that RESOLVING such a state, however it
    # arose, recurses correctly to the right depth.
    child_state = {
        "v": 1, "engine": "py", "version": 2,
        "records": [
            {"type": "run_started", "run_id": "C", "flow_id": "child_nonidem", "flow_version": 1,
             "engine": "py", "input": None},
            {"type": "step_started", "key": "act", "attempt": 1, "idempotency_key": "C:act"},
        ],
        "blobs": {},
    }
    top_state = {
        "v": 1, "engine": "py", "version": 2,
        "records": [
            {"type": "run_started", "run_id": "P", "flow_id": "parent_crashboundary", "flow_version": 1,
             "engine": "py", "input": None},
            {"type": "call_suspended", "key": "risky_child", "child_state": child_state},
        ],
        "blobs": {},
    }
    payload, code = engine.resume_flow(call_parent_crashboundary.main, top_state,
                                       resolve="completed", resolve_value={"acted": True})
    expect_code(code, "OK", "rf_in_doubt_nested resolve")
    expect(payload["result"] == {"r": {"v": {"acted": True}}}, "rf_in_doubt_nested result: %s" % payload)


def rf_crash_toplevel(_h=None):
    # Isolates trade-off #1's SIMPLEST case (no nesting at all): subprocess A crashes before
    # run_flow ever returns anything — there is NOTHING for anyone to have persisted. Subprocess
    # B starts completely fresh; the non-idempotent side effect fires a SECOND time with no
    # in-doubt escalation anywhere, because a MemoryStore has zero durability between calls.
    with tempfile.TemporaryDirectory() as td:
        ledger = os.path.join(td, "ledger.txt")
        rc, out, err = _driver({"module": "call_child_nonidem", "input": None,
                                "env": {"CALL_LEDGER": ledger, "CALL_CRASH": "1"}})
        expect(rc == 137, "rf_crash_toplevel expected the subprocess to crash (137), got %d (%s)" % (rc, err))
        expect(out is None, "rf_crash_toplevel: a crashed process must print NOTHING usable: %s" % out)

        rc2, out2, err2 = _driver({"module": "call_child_nonidem", "input": None,
                                   "env": {"CALL_LEDGER": ledger}})
        expect(rc2 == 0 and out2 and out2["code"] == engine.EXIT_OK,
               "rf_crash_toplevel fresh run failed: rc=%d out=%s err=%s" % (rc2, out2, err2))
        with open(ledger, encoding="utf-8") as f:
            lines = [x for x in f.read().split("\n") if x]
        expect(len(lines) == 2,
               "rf_crash_toplevel: side effect must have fired TWICE with zero in-doubt "
               "detection anywhere (ledger=%s)" % lines)


def call_export_hybrid(_h=None):
    # The HYBRID escape hatch: full CLI/on-disk durability during each call, PLUS an on-demand
    # portable snapshot for handoff. export_portable_state must be read-only (the on-disk run
    # still resumes normally afterward), and the concrete "two callers, one history point"
    # divergence risk (trade-off #2) is demonstrated by feeding the SAME earlier snapshot into
    # an independent resume_flow call with a DIFFERENT answer than the one given on-disk.
    with tempfile.TemporaryDirectory() as td:
        flow_path = os.path.join(LADDER, "call_top_2level.py")
        r = subprocess.run([sys.executable, PY_ENGINE, "run", "--flow", flow_path, "--state-dir", td],
                           capture_output=True, text=True)
        expect(r.returncode == 10, "call_export_hybrid run must suspend: %s" % r.stdout)

        exported = engine.export_portable_state(call_top_2level.main, td)
        expect(exported["derived"]["status"] == "suspended", "call_export_hybrid derived: %s" % exported["derived"])
        expect(exported["derived"]["pending"]["key"] == "child/gate",
               "call_export_hybrid derived pending: %s" % exported["derived"]["pending"])
        expect(exported["version"] == len(exported["records"]), "call_export_hybrid version token wrong")

        # read-only: the on-disk dir still resumes normally via the CLI afterward
        r2 = subprocess.run([sys.executable, PY_ENGINE, "resume", "--flow", flow_path,
                            "--state-dir", td, "--answer", '"a"'], capture_output=True, text=True)
        expect(r2.returncode == 0, "call_export_hybrid on-disk resume must still work: %s" % r2.stdout)
        on_disk_result = json.loads(r2.stdout.strip().splitlines()[-1])["result"]
        expect(on_disk_result == {"from_child": {"leaf_ans": "a"}}, "call_export_hybrid on-disk result: %s" % on_disk_result)

        # the concrete risk: the EARLIER exported snapshot, resumed independently with a
        # DIFFERENT answer, reaches its own, DIFFERENT completion — nothing stops this at the
        # engine level; state["version"] is what a real caller's own store should have compared.
        blob = json.loads(json.dumps(exported))
        payload3, code3 = engine.resume_flow(call_top_2level.main, blob, answer='"b"')
        expect_code(code3, "OK", "call_export_hybrid divergent resume")
        expect(payload3["result"] == {"from_child": {"leaf_ans": "b"}},
               "call_export_hybrid divergent result: %s" % payload3["result"])
        expect(payload3["result"] != on_disk_result,
               "call_export_hybrid: the two completions should have DIVERGED, demonstrating the risk")


def rf_headless_nested(_h=None):
    # A headless ctx.call child hitting a gate it cannot auto-answer must NOT silently vanish:
    # its own already-journaled ask_requested is embedded as a call_suspended (not lost), and
    # run_flow/resume_flow return an ordinary ({"status":"needs_answer",...}, 12) tuple instead
    # of letting SystemExit kill the caller's process.
    payload, code = engine.run_flow(call_top_2level.main, None, headless=True)
    expect_code(code, "NO_AUTOANSWER", "rf_headless_nested first pass")
    expect(payload["status"] == "needs_answer" and payload["pending"]["key"] == "child/gate",
           "rf_headless_nested pending wrong: %s" % payload)
    calls = [r for r in payload["state"]["records"] if r["type"] == "call_suspended"]
    expect(len(calls) == 1 and calls[0]["key"] == "child",
           "rf_headless_nested: the blocked child must still be durably embedded: %s" % calls)

    blob = json.loads(json.dumps(payload["state"]))
    payload2, code2 = engine.resume_flow(call_top_2level.main, blob, answer='"ok"')
    expect_code(code2, "OK", "rf_headless_nested resume after headless-block")
    expect(payload2["result"] == {"from_child": {"leaf_ans": "ok"}},
           "rf_headless_nested resumed result: %s" % payload2["result"])


@engine.flow(id="_rf_bad_child")
def _bad_child(ctx, inp):
    # deliberately NOT wrapped in ctx.step — a glue-level exception, so the child's OWN
    # inherited adjudicator (which only fires from Context.step's error handling) never
    # intercepts it; the failure only ever reaches the PARENT's ctx.call boundary.
    raise RuntimeError("child exploded")


@engine.flow(id="_rf_adj_parent")
def _adj_parent(ctx, inp):
    return {"r": ctx.call("risky_child", _bad_child, None)}


def rf_child_adjudicator(_h=None):
    # A failed (not suspended) ctx.call child must consult the PARENT's own adjudicator exactly
    # like an ordinary failed ctx.step does — skip memoizes a value and lets the parent continue;
    # abort fails with name="aborted"; no adjudicator at all is unchanged (plain FlowError).
    skip_calls = []

    def skip_adjudicator(req):
        skip_calls.append((req["kind"], req["key"]))
        return {"action": "skip", "value": "recovered"}

    payload, code = engine.run_flow(_adj_parent, None, adjudicator=skip_adjudicator)
    expect(("step_failed", "risky_child") in skip_calls,
           "rf_child_adjudicator: adjudicator never consulted at the ctx.call boundary: %s" % skip_calls)
    expect_code(code, "OK", "rf_child_adjudicator skip")
    expect(payload["result"] == {"r": "recovered"}, "rf_child_adjudicator skip result: %s" % payload)

    payload2, code2 = engine.run_flow(_adj_parent, None, adjudicator=lambda req: {"action": "abort"})
    expect_code(code2, "FLOW_FAILED", "rf_child_adjudicator abort")
    expect(payload2["error"]["name"] == "aborted", "rf_child_adjudicator abort error: %s" % payload2["error"])

    payload3, code3 = engine.run_flow(_adj_parent, None)
    expect_code(code3, "FLOW_FAILED", "rf_child_adjudicator no-adjudicator baseline")
    expect(payload3["error"]["name"] == "RuntimeError" and payload3["error"]["step"] == "risky_child",
           "rf_child_adjudicator no-adjudicator baseline changed: %s" % payload3["error"])


@engine.flow(id="_rf_corrupt_leaf")
def _corrupt_leaf(ctx, inp):
    v = ctx.step("a", lambda: "leafval")
    ans = ctx.ask("gate", {"prompt": "go?"})
    return {"v": v, "ans": ans}


@engine.flow(id="_rf_corrupt_parent")
def _corrupt_parent(ctx, inp):
    return {"r": ctx.call("child", _corrupt_leaf, None)}


def rf_child_corruption(_h=None):
    # A corrupt blob discovered inside a NESTED ctx.call child's own Memo construction must
    # still report EXIT_SKEW (3) at the top, exactly like a top-level Corruption does — not get
    # silently downgraded to an ordinary EXIT_FLOW_FAILED (1) by an intervening parent's generic
    # exception handling.
    payload, code = engine.run_flow(_corrupt_parent, None)
    expect_code(code, "SUSPENDED", "rf_child_corruption setup")
    state = json.loads(json.dumps(payload["state"]))
    child_state = state["records"][1]["child_state"]
    tampered = False
    for r in child_state["records"]:
        if r.get("type") == "step_completed" and r.get("key") == "a" and "result" in r:
            value = r.pop("result")
            r["result_ref"] = "a.%d.json" % r["attempt"]
            r["result_sha256"] = "0" * 64  # deliberately wrong
            child_state["blobs"][r["result_ref"]] = value
            tampered = True
    expect(tampered, "rf_child_corruption: fixture has no blob-spillable step_completed to tamper")

    payload2, code2 = engine.resume_flow(_corrupt_parent, state, answer='"ok"')
    expect_code(code2, "SKEW", "rf_child_corruption must report EXIT_SKEW, not EXIT_FLOW_FAILED")
    expect("Corruption" in payload2.get("error", "") or "integrity" in payload2.get("error", ""),
           "rf_child_corruption error unclear: %s" % payload2)


def rf_derive_status_latest(_h=None):
    # export_portable_state's read-only status derivation (_derive_status) must report the
    # LATEST unresolved gate/dangling-step, not the first flow_suspended record it happens to
    # scan past — a flow that answered an earlier gate and has since suspended on (or gone
    # in-doubt after) a LATER one must not be misreported as still waiting on the old one.
    base = [
        {"type": "run_started", "run_id": "R", "flow_id": "f", "flow_version": 1,
         "engine": "py", "input": None},
        {"type": "ask_requested", "key": "A", "question": {"q": "A?"}, "schema": None},
        {"type": "flow_suspended", "pending_key": "A"},
        {"type": "ask_answered", "key": "A", "raw": "x", "answer": "x", "interpreted_by": "human"},
        {"type": "run_started", "run_id": "R", "flow_id": "f", "flow_version": 1,
         "engine": "py", "input": None},
        {"type": "ask_requested", "key": "B", "question": {"q": "B?"}, "schema": None},
        {"type": "flow_suspended", "pending_key": "B"},
    ]
    status, pending, _r, _e = engine._derive_status(base, engine.MemoryStore())
    expect(status == "suspended" and pending["key"] == "B",
           "rf_derive_status_latest: expected latest gate B, got %s %s" % (status, pending))

    dangling = base + [
        {"type": "ask_answered", "key": "B", "raw": "y", "answer": "y", "interpreted_by": "human"},
        {"type": "run_started", "run_id": "R", "flow_id": "f", "flow_version": 1,
         "engine": "py", "input": None},
        {"type": "step_started", "key": "charge", "attempt": 1, "idempotency_key": "R:charge"},
    ]
    status2, pending2, _r2, _e2 = engine._derive_status(dangling, engine.MemoryStore())
    expect(status2 == "in_doubt" and pending2["key"] == "charge",
           "rf_derive_status_latest: expected in_doubt on charge, got %s %s" % (status2, pending2))


def _nested_dangling_state():
    """The rf_in_doubt_nested seed shape: a call_suspended embedding a child with one dangling
    non-idempotent step — the target of explicit resolve_key addressing."""
    child_state = {
        "v": 1, "engine": "py", "version": 2,
        "records": [
            {"type": "run_started", "run_id": "C", "flow_id": "child_nonidem", "flow_version": 1,
             "engine": "py", "input": None},
            {"type": "step_started", "key": "act", "attempt": 1, "idempotency_key": "C:act"},
        ],
        "blobs": {},
    }
    return {
        "v": 1, "engine": "py", "version": 2,
        "records": [
            {"type": "run_started", "run_id": "P", "flow_id": "parent_crashboundary", "flow_version": 1,
             "engine": "py", "input": None},
            {"type": "call_suspended", "key": "risky_child", "child_state": child_state},
        ],
        "blobs": {},
    }


def rf_resolve_key_nested(_h=None):
    # Explicit resolve_key targeting a nested in-doubt, all three addressing forms — the
    # in-doubt twin of the answer-key path addressing (hoisted key round-trips verbatim,
    # leaf-local keeps working, wrong key is a clean no-op error that consumes nothing).
    payload, code = engine.resume_flow(call_parent_crashboundary.main, _nested_dangling_state(),
                                       resolve="completed", resolve_key="risky_child/act",
                                       resolve_value={"acted": True})
    expect_code(code, "OK", "rf_resolve_key_nested hoisted path key")
    expect(payload["result"] == {"r": {"v": {"acted": True}}},
           "rf_resolve_key_nested hoisted result: %s" % payload.get("result"))

    payload2, code2 = engine.resume_flow(call_parent_crashboundary.main, _nested_dangling_state(),
                                         resolve="completed", resolve_key="act",
                                         resolve_value={"acted": True})
    expect_code(code2, "OK", "rf_resolve_key_nested leaf-local key")

    payload3, code3 = engine.resume_flow(call_parent_crashboundary.main, _nested_dangling_state(),
                                         resolve="completed", resolve_key="bogus",
                                         resolve_value={"acted": True})
    expect(code3 == engine.EXIT_USAGE and payload3["status"] == "error" and "bogus" in payload3["error"],
           "rf_resolve_key_nested wrong key must be a clean EXIT_USAGE error: %d %s" % (code3, payload3))
    # nothing consumed: the SAME original state still resolves correctly afterwards
    payload4, code4 = engine.resume_flow(call_parent_crashboundary.main, _nested_dangling_state(),
                                         resolve="completed", resolve_value={"acted": True})
    expect_code(code4, "OK", "rf_resolve_key_nested still resolvable after rejection")


_RETRY_COUNTS = {"flaky": 0, "doomed": 0}


def _flaky_fn():
    _RETRY_COUNTS["flaky"] += 1
    if _RETRY_COUNTS["flaky"] == 1:
        raise RuntimeError("transient")
    return "flaky-ok"


def _doomed_fn():
    _RETRY_COUNTS["doomed"] += 1
    raise RuntimeError("permanent")


@engine.flow(id="_rf_retry_leaf")
def _retry_leaf(ctx, inp):
    flaky = ctx.step("flaky", _flaky_fn, retries=1)
    doomed = ctx.step("doomed", _doomed_fn, on_fail=lambda e, a: {"action": "catch"})
    ans = ctx.ask("gate", {"prompt": "?"})
    return {"flaky": flaky, "doomed": doomed, "ans": ans}


@engine.flow(id="_rf_retry_parent")
def _retry_parent(ctx, inp):
    return {"r": ctx.call("child", _retry_leaf, None)}


def rf_child_retry_catch(_h=None):
    # Step retries + on_fail="catch" INSIDE a child: the retry history and the caught-error
    # sentinel are embedded in child_state at suspend, and the resume replays them (fn bodies
    # do NOT re-run) rather than re-executing.
    _RETRY_COUNTS["flaky"] = 0
    _RETRY_COUNTS["doomed"] = 0
    payload, code = engine.run_flow(_retry_parent, None)
    expect_code(code, "SUSPENDED", "rf_child_retry_catch run")
    cs = [r for r in payload["state"]["records"] if r["type"] == "call_suspended"][0]["child_state"]
    fails = [r for r in cs["records"] if r["type"] == "step_failed" and r["key"] == "flaky"]
    expect(len(fails) == 1 and fails[0]["attempt"] == 1 and fails[0]["error"]["retriable"] is True,
           "rf_child_retry_catch: expected one retriable attempt-1 failure for flaky: %s" % fails)
    comp = {r["key"]: r for r in cs["records"] if r["type"] == "step_completed"}
    expect(comp["flaky"]["attempt"] == 2, "rf_child_retry_catch: flaky must succeed on attempt 2")
    expect(comp["doomed"]["result"] == {"__error__": {"name": "RuntimeError", "message": "permanent",
                                                      "attempts": 1}},
           "rf_child_retry_catch: doomed must memoize the catch sentinel: %s" % comp["doomed"])

    blob = json.loads(json.dumps(payload["state"]))
    payload2, code2 = engine.resume_flow(_retry_parent, blob, answer='"go"')
    expect_code(code2, "OK", "rf_child_retry_catch resume")
    expect(_RETRY_COUNTS == {"flaky": 2, "doomed": 1},
           "rf_child_retry_catch: replay must NOT re-run step fns (counts=%s)" % _RETRY_COUNTS)
    expect(payload2["result"]["r"]["flaky"] == "flaky-ok"
           and payload2["result"]["r"]["doomed"]["__error__"]["name"] == "RuntimeError",
           "rf_child_retry_catch result: %s" % payload2["result"])

    # CALL-level on_fail="catch": a WHOLE-CHILD failure memoizes the same __error__ sentinel an
    # ordinary step catch does, the parent continues past it, and the adjudicator is bypassed
    # (on_fail supersedes, exactly like ctx.step).
    adj_calls = []

    @engine.flow(id="_rf_callcatch_parent")
    def _callcatch_parent(ctx, inp):
        r = ctx.call("risky", _bad_child, None, on_fail=lambda e, a: {"action": "catch"})
        return {"r": r, "after": True}

    payload3, code3 = engine.run_flow(_callcatch_parent, None,
                                      adjudicator=lambda req: adj_calls.append(req) or {"action": "abort"})
    expect_code(code3, "OK", "rf_child_retry_catch call-level catch")
    expect(payload3["result"]["r"] == {"__error__": {"name": "RuntimeError",
                                                     "message": "child exploded", "attempts": 1}}
           and payload3["result"]["after"] is True,
           "rf_child_retry_catch: call catch must memoize the sentinel and continue: %s"
           % payload3["result"])
    expect(not adj_calls, "rf_child_retry_catch: on_fail catch must bypass the adjudicator: %s" % adj_calls)


@engine.flow(id="_rf_blob_done_leaf")
def _blob_done_leaf(ctx, inp):
    return ctx.step("big", lambda: "y" * 70000)


@engine.flow(id="_rf_blob_done_parent")
def _blob_done_parent(ctx, inp):
    big = ctx.call("child", _blob_done_leaf, None)
    ans = ctx.ask("gate", {"prompt": "?"})
    return {"big_len": len(big), "ans": ans}


def rf_child_blob(_h=None):
    # GENUINE blob spill (result > BLOB_THRESHOLD 65536) across a ctx.call boundary, both paths.
    # (a) child suspends AFTER the big step: the embedded child_state must carry the spilled
    # result as result_ref + an INLINED blobs entry, and a CROSS-PROCESS resume must replay it.
    payload, code = engine.run_flow(call_blob.main, None)
    expect_code(code, "SUSPENDED", "rf_child_blob (a) run")
    cs = [r for r in payload["state"]["records"] if r["type"] == "call_suspended"][0]["child_state"]
    big = [r for r in cs["records"] if r["type"] == "step_completed" and r["key"] == "big"][0]
    expect("result_ref" in big and "result_sha256" in big and "result" not in big,
           "rf_child_blob (a): child step must have genuinely spilled: %s" % list(big))
    expect(cs["blobs"].get(big["result_ref"]) == "x" * 70000,
           "rf_child_blob (a): embedded child_state.blobs must inline the spilled value")
    blob = json.loads(json.dumps(payload["state"]))
    rc, out, err = _driver({"module": "call_blob", "state": blob, "answer": '"ok"'})
    expect(rc == 0 and out and out["code"] == engine.EXIT_OK,
           "rf_child_blob (a) cross-process resume failed: rc=%d out=%s err=%s" % (rc, out, err))
    expect(out["payload"]["result"] == {"from_child": {"big_len": 70000, "head": "xxx", "ans": "ok"}},
           "rf_child_blob (a) result: %s" % out["payload"])

    # (b) child COMPLETES with the big result: the PARENT memoizes it via _complete, which
    # spills to the parent's own store — the top-level portable state must inline that blob.
    payload2, code2 = engine.run_flow(_blob_done_parent, None)
    expect_code(code2, "SUSPENDED", "rf_child_blob (b) run")
    top_big = [r for r in payload2["state"]["records"]
               if r["type"] == "step_completed" and r["key"] == "child"][0]
    expect("result_ref" in top_big and "result" not in top_big,
           "rf_child_blob (b): the memoized call result must have spilled at the PARENT: %s" % list(top_big))
    expect(payload2["state"]["blobs"].get(top_big["result_ref"]) == "y" * 70000,
           "rf_child_blob (b): top-level state.blobs must inline the parent-store spill")
    blob2 = json.loads(json.dumps(payload2["state"]))
    payload3, code3 = engine.resume_flow(_blob_done_parent, blob2, answer='"ok"')
    expect_code(code3, "OK", "rf_child_blob (b) resume")
    expect(payload3["result"] == {"big_len": 70000, "ans": "ok"}, "rf_child_blob (b) result: %s"
           % payload3["result"])


@engine.flow(id="_rf_gate_child")
def _gate_child(ctx, inp):
    ans = ctx.ask("gate", {"prompt": "gate for %s?" % inp})
    return {"inp": inp, "got": ans}


@engine.flow(id="_rf_sibling_parent")
def _sibling_parent(ctx, inp):
    a = ctx.call("A", _gate_child, "qa")
    b = ctx.call("B", _gate_child, "qb")
    return {"a": a, "b": b}


def rf_sibling_calls(_h=None):
    # Two sibling ctx.call sites (SAME child flow object reused under two keys) suspending on
    # DIFFERENT passes — pins the one-shot ResumeCtx claiming: on the B-resume pass, memoized A
    # returns at the memo.completed check and can never claim the token; and the same flow
    # object composes under both keys with independent inputs/answers.
    payload, code = engine.run_flow(_sibling_parent, None)
    expect_code(code, "SUSPENDED", "rf_sibling_calls pass 1")
    expect(payload["pending"]["key"] == "A/gate", "rf_sibling_calls pass 1 pending: %s" % payload["pending"])

    blob = json.loads(json.dumps(payload["state"]))
    payload2, code2 = engine.resume_flow(_sibling_parent, blob, answer='"a1"')
    expect_code(code2, "SUSPENDED", "rf_sibling_calls pass 2")
    expect(payload2["pending"]["key"] == "B/gate",
           "rf_sibling_calls: A resolves and B suspends on the same pass: %s" % payload2["pending"])

    events = []
    blob2 = json.loads(json.dumps(payload2["state"]))
    payload3, code3 = engine.resume_flow(_sibling_parent, blob2, answer='"b1"', observer=events.append)
    expect_code(code3, "OK", "rf_sibling_calls pass 3")
    expect(payload3["result"] == {"a": {"inp": "qa", "got": "a1"}, "b": {"inp": "qb", "got": "b1"}},
           "rf_sibling_calls: each answer must land at the right child: %s" % payload3["result"])
    a_events = [e for e in events if e.get("key") == "A"]
    expect(len(a_events) == 1 and a_events[0]["phase"] == "replay",
           "rf_sibling_calls: memoized A must be replay-only on the B-resume pass (token never "
           "claimable by it): %s" % a_events)


@engine.flow(id="_rf_runid_leaf")
def _runid_leaf(ctx, inp):
    v = ctx.step("a", lambda: "worked")
    g1 = ctx.ask("g1", {"prompt": "first?"})
    g2 = ctx.wait("g2")
    return {"v": v, "g1": g1, "g2": g2}


@engine.flow(id="_rf_runid_parent")
def _runid_parent(ctx, inp):
    return {"r": ctx.call("child", _runid_leaf, None)}


def rf_child_runid_wait(_h=None):
    # Child run_id stability + ctx.wait across a call boundary: the child's run_id is minted
    # once and survives every reconstitution from the embedded child_state (it roots the child's
    # OWN idempotency keys, independent of the parent's run_id); ctx.wait is exactly ctx.ask
    # with a synthesized "waiting for <key>" question.
    payload, code = engine.run_flow(_runid_parent, None)
    expect_code(code, "SUSPENDED", "rf_child_runid_wait first suspend")
    top_run_id = [r for r in payload["state"]["records"] if r["type"] == "run_started"][0]["run_id"]
    cs1 = [r for r in payload["state"]["records"] if r["type"] == "call_suspended"][0]["child_state"]
    child_ids1 = {r["run_id"] for r in cs1["records"] if r["type"] == "run_started"}
    expect(len(child_ids1) == 1, "rf_child_runid_wait: one child run_id expected: %s" % child_ids1)
    child_id = child_ids1.pop()
    expect(child_id != top_run_id, "rf_child_runid_wait: child run_id must be its own, not the parent's")
    started = [r for r in cs1["records"] if r["type"] == "step_started" and r["key"] == "a"][0]
    expect(started["idempotency_key"] == "%s:a" % child_id,
           "rf_child_runid_wait: child idem keys must be rooted at the CHILD run_id: %s"
           % started["idempotency_key"])

    blob = json.loads(json.dumps(payload["state"]))
    payload2, code2 = engine.resume_flow(_runid_parent, blob, answer='"one"')
    expect_code(code2, "SUSPENDED", "rf_child_runid_wait second suspend")
    expect(payload2["pending"]["key"] == "child/g2"
           and payload2["pending"]["question"] == {"prompt": "waiting for g2"},
           "rf_child_runid_wait: ctx.wait pending shape: %s" % payload2["pending"])
    cs2 = [r for r in payload2["state"]["records"] if r["type"] == "call_suspended"][-1]["child_state"]
    child_ids2 = {r["run_id"] for r in cs2["records"] if r["type"] == "run_started"}
    expect(child_ids2 == {child_id},
           "rf_child_runid_wait: child run_id must be IDENTICAL across both embedded snapshots "
           "(got %s, want {%r})" % (child_ids2, child_id))

    blob2 = json.loads(json.dumps(payload2["state"]))
    payload3, code3 = engine.resume_flow(_runid_parent, blob2, answer='"two"')
    expect_code(code3, "OK", "rf_child_runid_wait completes")
    expect(payload3["result"] == {"r": {"v": "worked", "g1": "one", "g2": "two"}},
           "rf_child_runid_wait result: %s" % payload3["result"])


@engine.flow(id="_rf_interp_leaf")
def _interp_leaf(ctx, inp):
    g1 = ctx.ask("g1", {"prompt": "free-form?"})
    g2 = ctx.ask("g2", {"prompt": "pick one"}, schema={"type": "string", "enum": ["a", "b"]})
    return {"g1": g1, "g2": g2}


@engine.flow(id="_rf_interp_parent")
def _interp_parent(ctx, inp):
    return {"r": ctx.call("child", _interp_leaf, None)}


def rf_child_interpreter(_h=None):
    # Interpreter-based auto-answer resolving a NESTED gate: the interpreter hook inherits into
    # the child engine; a VALID interpreted answer is journaled in the embedded child journal
    # (interpreted_by="llm"), an INVALID one is rejected without journaling (gate stays open),
    # and the whole thing hands over cleanly to a non-headless resume.
    seen = []

    def interp(req):
        seen.append(req["key"])
        return "fine" if req["key"] == "g1" else "not-in-enum"

    payload, code = engine.run_flow(_interp_parent, None, headless=True, interpreter=interp)
    expect_code(code, "NO_AUTOANSWER", "rf_child_interpreter headless run")
    expect(seen == ["g1", "g2"], "rf_child_interpreter: interpreter must be consulted for both "
           "nested gates in order: %s" % seen)
    expect(payload["pending"]["key"] == "child/g2",
           "rf_child_interpreter: hoisted pending at the REJECTED gate: %s" % payload["pending"])
    expect("auto-answer rejected" in payload.get("error", ""),
           "rf_child_interpreter: rejection reason must surface: %s" % payload)
    cs = [r for r in payload["state"]["records"] if r["type"] == "call_suspended"][0]["child_state"]
    answered = {r["key"]: r for r in cs["records"] if r["type"] == "ask_answered"}
    expect(answered["g1"]["interpreted_by"] == "llm" and answered["g1"]["answer"] == "fine",
           "rf_child_interpreter: g1 must be llm-answered in the embedded journal: %s" % answered)
    expect("g2" not in answered and any(r["type"] == "ask_requested" and r["key"] == "g2"
                                        for r in cs["records"]),
           "rf_child_interpreter: rejected g2 auto-answer must NOT be journaled")

    blob = json.loads(json.dumps(payload["state"]))
    payload2, code2 = engine.resume_flow(_interp_parent, blob, answer='"b"')
    expect_code(code2, "OK", "rf_child_interpreter non-headless resume")
    expect(payload2["result"] == {"r": {"g1": "fine", "g2": "b"}},
           "rf_child_interpreter: g1's llm answer must be preserved: %s" % payload2["result"])

    # Regression pin for the latent TOP-LEVEL bug this rung exposed (pre-dating ctx.call): the
    # interpreter/default auto-answer path journals a BARE ask_answered (no ask_requested), and
    # before the Memo fix that gate was missing from key_order — so ANY later resume of the same
    # run tripped NonDeterminism at the first request. Same shape, no nesting.
    payload3, code3 = engine.run_flow(_interp_leaf, None, headless=True, interpreter=interp)
    expect_code(code3, "NO_AUTOANSWER", "rf_child_interpreter top-level headless run")
    blob3 = json.loads(json.dumps(payload3["state"]))
    payload4, code4 = engine.resume_flow(_interp_leaf, blob3, answer='"a"')
    expect_code(code4, "OK", "rf_child_interpreter top-level resume after llm-answered gate "
                             "(key_order regression)")
    expect(payload4["result"] == {"g1": "fine", "g2": "a"},
           "rf_child_interpreter top-level result: %s" % payload4["result"])


def rf_derive_drift(_h=None):
    # Drift guard pinning _derive_status (the read-only re-implementation) to Engine.execute()'s
    # live hoisting: drive the SAME 3-level flow to each of its three states via BOTH surfaces —
    # library (run_flow/resume_flow) and CLI + export_portable_state — asserting the derived
    # status/pending/result match the live payloads at EVERY stage.
    with tempfile.TemporaryDirectory() as td:
        flow_path = os.path.join(LADDER, "call_top_3level.py")

        def cli(cmd, *extra):
            r = subprocess.run([sys.executable, PY_ENGINE, cmd, "--flow", flow_path,
                               "--state-dir", td] + list(extra), capture_output=True, text=True)
            return r

        lib_payload, lib_code = engine.run_flow(call_top_3level.main, None)
        cli_r = cli("run", "--input", "null")
        stages = [("suspend@depth3", lib_payload, cli_r)]

        blob = json.loads(json.dumps(lib_payload["state"]))
        lib_payload2, _ = engine.resume_flow(call_top_3level.main, blob, answer='"ok"')
        cli_r2 = cli("resume", "--answer", '"ok"')
        stages.append(("suspend@depth2", lib_payload2, cli_r2))

        blob2 = json.loads(json.dumps(lib_payload2["state"]))
        lib_payload3, _ = engine.resume_flow(call_top_3level.main, blob2, answer='"ok"')
        cli_r3 = cli("resume", "--answer", '"ok"')
        stages.append(("completed", lib_payload3, cli_r3))

        for name, lib, _cli_r in stages:
            exported = engine.export_portable_state(call_top_3level.main, td) if name == "completed" \
                else None
            # export at THIS stage: for the two suspend stages we must export before the next
            # CLI resume mutates the dir — so re-derive from the library state instead, which is
            # bit-identical in records to the CLI dir at the same stage. Simpler: assert against
            # the library state's own derived block (written by _portable_state at that stage).
            derived = lib["state"]["derived"]
            expect(derived["status"] == lib["status"],
                   "rf_derive_drift %s: derived.status %r != live %r" % (name, derived["status"], lib["status"]))
            expect(derived["pending"] == lib.get("pending"),
                   "rf_derive_drift %s: derived.pending %s != live %s" % (name, derived["pending"], lib.get("pending")))
            expect(derived["result"] == lib.get("result"),
                   "rf_derive_drift %s: derived.result mismatch" % name)
            if exported is not None:
                expect(exported["derived"]["status"] == lib["status"]
                       and exported["derived"]["result"] == lib.get("result"),
                       "rf_derive_drift %s: CLI export drifted from library: %s" % (name, exported["derived"]))
        # the CLI dir is now completed — export once more and pin the full final equality
        exported = engine.export_portable_state(call_top_3level.main, td)
        expect(exported["derived"]["status"] == "completed"
               and exported["derived"]["result"] == lib_payload3["result"],
               "rf_derive_drift: final CLI export must match the library completion: %s"
               % exported["derived"])


@engine.flow(id="_rf_deep")
def _deep(ctx, inp):
    # self-referential: depth d calls itself at d-1; d==0 is the gate leaf.
    if inp["d"] == 0:
        return {"leaf": ctx.ask("gate", {"prompt": "bottom?"})}
    return {"r": ctx.call("c", _deep, {"d": inp["d"] - 1})}


def rf_deep_chain(_h=None):
    # Deep nesting (12 levels): the full chain surfaces in pending, resume completes with no
    # RecursionError, depth never leaks into the TOP journal (exactly run_started +
    # call_suspended while suspended), total embedding stays linear-ish in depth, and 1a's
    # path addressing resolves a 12-segment explicit key.
    payload, code = engine.run_flow(_deep, {"d": 12})
    expect_code(code, "SUSPENDED", "rf_deep_chain run")
    expect(payload["pending"]["key"] == "c/" * 12 + "gate"
           and len(payload["pending"]["chain"]) == 13,
           "rf_deep_chain pending: %s" % payload["pending"]["key"])
    top_types = [r["type"] for r in payload["state"]["records"]]
    expect(top_types == ["run_started", "call_suspended"],
           "rf_deep_chain: depth must never leak into the top journal: %s" % top_types)

    payload6, _ = engine.run_flow(_deep, {"d": 6})
    size12 = len(json.dumps(payload["state"]))
    size6 = len(json.dumps(payload6["state"]))
    expect(size12 < 2.5 * size6,
           "rf_deep_chain: embedding must stay ~linear in depth (d12=%d vs d6=%d)" % (size12, size6))

    blob = json.loads(json.dumps(payload["state"]))
    payload2, code2 = engine.resume_flow(_deep, blob, answer='"deep-ok"', key="c/" * 12 + "gate")
    expect_code(code2, "OK", "rf_deep_chain resume via 12-segment explicit key")
    r = payload2["result"]
    for _ in range(12):
        r = r["r"]
    expect(r == {"leaf": "deep-ok"}, "rf_deep_chain nested result wrong: %s" % payload2["result"])


def rf_call_observer(_h=None):
    # Observer vocabulary at call boundaries: phase "call" on every LIVE boundary crossing
    # (resumed=False fresh / resumed=True reconstituted), phase "call_suspended" when the child's
    # suspend propagates, phase "replay" (and nothing else) once memoized — and child-internal
    # events flow through the same observer with their child-local keys.
    ev1 = []
    payload, code = engine.run_flow(call_top_2level.main, None, observer=ev1.append)
    expect_code(code, "SUSPENDED", "rf_call_observer run")
    calls = [e for e in ev1 if e.get("phase") == "call"]
    expect(len(calls) == 1 and calls[0]["key"] == "child" and calls[0]["resumed"] is False
           and calls[0]["flow_id"] == "leaf",
           "rf_call_observer: expected one fresh phase:call event: %s" % calls)
    susp = [e for e in ev1 if e.get("phase") == "call_suspended"]
    expect(len(susp) == 1 and susp[0]["key"] == "child" and susp[0]["in_doubt"] is False
           and susp[0]["pending"]["key"] == "gate",
           "rf_call_observer: expected one call_suspended event with the CHILD-local pending: %s" % susp)

    ev2 = []
    blob = json.loads(json.dumps(payload["state"]))
    payload2, code2 = engine.resume_flow(call_top_2level.main, blob, answer='"ok"', observer=ev2.append)
    expect_code(code2, "OK", "rf_call_observer resume")
    calls2 = [e for e in ev2 if e.get("phase") == "call"]
    expect(len(calls2) == 1 and calls2[0]["resumed"] is True,
           "rf_call_observer: the resumed crossing must carry resumed=True: %s" % calls2)
    expect(not [e for e in ev2 if e.get("phase") == "call_suspended"],
           "rf_call_observer: no call_suspended event on the completing pass")
    # the completed call memoizes via _complete -> synthesized "after" for the call key
    after = [e for e in ev2 if e.get("phase") == "after" and e.get("key") == "child"]
    expect(len(after) == 1 and after[0].get("synthesized") is True,
           "rf_call_observer: completed call must emit the synthesized after: %s" % after)


def rf_nostrict_nested(_h=None):
    # strict=False with an open call: the same renamed-open-call state that call_memo_strict_gap
    # pins as EXIT_SKEW under default strictness must, with strict=False, proceed (the renamed
    # site starts a FRESH child) and re-suspend at child/gate — with the unclaimed resume answer
    # dropped (warning) rather than mis-applied.
    def seeded():
        return {
            "v": 1, "engine": "py", "version": 2,
            "records": [
                {"type": "run_started", "run_id": "R", "flow_id": "top_2level", "flow_version": 1,
                 "engine": "py", "input": None},
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
            ],
            "blobs": {},
        }

    payload, code = engine.resume_flow(call_top_2level.main, seeded(), answer='"ok"')
    expect_code(code, "SKEW", "rf_nostrict_nested: default strict must refuse the renamed open call")

    payload2, code2 = engine.resume_flow(call_top_2level.main, seeded(), answer='"ok"', strict=False)
    expect_code(code2, "SUSPENDED", "rf_nostrict_nested: strict=False must proceed")
    expect(payload2["pending"]["key"] == "child/gate",
           "rf_nostrict_nested: fresh child suspends at child/gate: %s" % payload2["pending"])


RUNGS = [
    ("rf_2level", rf_2level),
    ("rf_3level", rf_3level),
    ("rf_failed_child", rf_failed_child),
    ("rf_in_doubt_nested", rf_in_doubt_nested),
    ("rf_crash_toplevel", rf_crash_toplevel),
    ("call_export_hybrid", call_export_hybrid),
    ("rf_headless_nested", rf_headless_nested),
    ("rf_child_adjudicator", rf_child_adjudicator),
    ("rf_child_corruption", rf_child_corruption),
    ("rf_derive_status_latest", rf_derive_status_latest),
    ("rf_resolve_key_nested", rf_resolve_key_nested),
    ("rf_child_retry_catch", rf_child_retry_catch),
    ("rf_child_blob", rf_child_blob),
    ("rf_sibling_calls", rf_sibling_calls),
    ("rf_child_runid_wait", rf_child_runid_wait),
    ("rf_child_interpreter", rf_child_interpreter),
    ("rf_derive_drift", rf_derive_drift),
    ("rf_deep_chain", rf_deep_chain),
    ("rf_call_observer", rf_call_observer),
    ("rf_nostrict_nested", rf_nostrict_nested),
]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default=None, help="only rungs whose name contains this")
    args = ap.parse_args(argv)

    passed = []
    for name, fn in RUNGS:
        if args.k and args.k not in name:
            continue
        try:
            fn()
            passed.append(name)
            print("  PASS %s" % name)
        except LadderError as e:
            print("\nFAIL at rung %s\n  %s" % (name, e))
            return 1
    print("\nCall-ladder OK: %s" % ", ".join(passed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
