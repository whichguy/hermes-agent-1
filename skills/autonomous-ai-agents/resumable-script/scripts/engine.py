#!/usr/bin/env python3
"""resumable-script engine — "durable execution, lite" (Python reference).

A flow is an ordinary function ``(ctx, input) -> result``. Side effects go through
``ctx.step(key, fn)``; human/external gates go through ``ctx.ask(key, question)`` /
``ctx.wait(...)``. One append-only ``journal.jsonl`` per run. To resume, the flow is
re-run from the top: a step whose key already has a ``step_completed`` record returns
the recorded result without executing; an unanswered gate raises ``Suspend`` and the
process exits. The user's answer is appended to the journal and present on replay.

Exit codes:
  0   flow completed
  1   flow failed (terminal error after retries)
  2   engine/usage error (bad args, can't load flow, KeyCollision)
  3   non-determinism / replay skew (refuse to resume)
  10  suspended, awaiting an answer
  11  suspended for in-doubt adjudication (non-idempotent step interrupted)
  12  headless run could not auto-answer
  13  busy (another process holds the run lock)

CLI:
  python3 engine.py run    --flow <path> [--input '<json>'] [--state-dir <dir>] [--auto] [--strict]
  python3 engine.py resume --flow <path> --answer '<json-or-text>' [--key <k>] [--state-dir <dir>]
"""
import argparse
import errno
import fcntl
import hashlib
import inspect
import json
import os
import random as _random_mod
import sys
import time as _time_mod
import uuid as _uuid_mod

# Real entropy sources captured once, so ctx.* helpers keep working even when the
# raw module functions are shadowed during flow execution (strict mode).
_REAL_TIME = _time_mod.time
_REAL_RANDOM = _random_mod.random
_REAL_UUID = _uuid_mod.uuid4

SCHEMA_V = 1
BLOB_THRESHOLD = int(os.environ.get("HERMES_FLOW_BLOB_THRESHOLD", "65536"))
# bc: warn-only ceiling for one exported portable-state value (all blobs INLINE — see
# references/nested-flows.md "Deferred"/external-blob escape hatch); 0 disables the warning.
PORTABLE_STATE_WARN_BYTES = int(os.environ.get("HERMES_FLOW_PORTABLE_WARN", str(8 * 1024 * 1024)))
MAX_SAFE_INT = (2 ** 53) - 1

EXIT_OK = 0
EXIT_FLOW_FAILED = 1
EXIT_USAGE = 2
EXIT_SKEW = 3
EXIT_SUSPENDED = 10
EXIT_IN_DOUBT = 11
EXIT_NO_AUTOANSWER = 12
EXIT_BUSY = 13

# ── ORIENTATION (grep "bc:" for breadcrumbs) ─────────────────────────────────
# One invocation (run OR resume) is one pass of Engine.execute():
#   read journal -> Memo (replay view) -> append run_started -> call the flow fn
#   flow calls ctx.step / ctx.ask -> Context._request guards each -> then:
#       step:  completed-in-journal? return it (REPLAY, fn NOT run) : run+journal (EXECUTE)
#       ask:   answered-in-journal?  return it                       : raise Suspend
#   the exception that escapes the flow IS the result: Suspend=10 InDoubt=11
#   KeyCollision=2 NonDeterminism=3 FlowError=1, clean return=0.
# RESUME == re-run the same fn from the top; journaled steps replay instantly, so
# only new work executes. The call stack is never serialized — it is rebuilt by replay.
# Parts: Store (io+lock) · Memo (derived replay state) · Context (step/ask) · Engine (conductor)
# On-disk contract (shared with engine.js): references/journal-format.md
# ─────────────────────────────────────────────────────────────────────────────


# ----------------------------------------------------------------------------- errors / signals
class Suspend(Exception):
    def __init__(self, key, question, schema, kind="ask"):
        super().__init__("suspend:%s" % key)
        self.key = key
        self.question = question
        self.schema = schema
        self.kind = kind


class InDoubt(Exception):
    def __init__(self, key, attempt):
        super().__init__("in_doubt:%s" % key)
        self.key = key
        self.attempt = attempt


class ChildSuspend(Exception):
    # bc: a ctx.call child came back suspended. Distinct from Suspend (not a subclass) so a
    # stray bare `except Suspend:` written elsewhere in this file can never silently swallow a
    # child boundary and mishandle it as an ordinary top-level suspend. Caught by
    # Engine.execute() and converted into THIS level's own suspended payload (_hoist_pending),
    # which — if this level is itself a ctx.call child — becomes the NEXT ChildSuspend up,
    # composing to arbitrary depth via ordinary exception propagation (no manual tree-walk).
    # NOTE: this does NOT protect against ctx.call being called from INSIDE a ctx.step's fn —
    # that's already a cardinal-rule violation (references/authoring-flows.md: side effects only
    # inside ctx.step, code between steps must be pure) and Context.step's own broad
    # `except Exception` there would swallow this — or a plain Suspend/InDoubt — identically.
    def __init__(self, key, child_state, child_pending):
        super().__init__("child_suspend:%s" % key)
        self.key = key
        self.child_state = child_state
        self.child_pending = child_pending


class ChildInDoubt(Exception):
    # bc: the in-doubt analogue of ChildSuspend — a ctx.call child came back in_doubt.
    def __init__(self, key, child_state, child_pending):
        super().__init__("child_in_doubt:%s" % key)
        self.key = key
        self.child_state = child_state
        self.child_pending = child_pending


class ResumeReject(Exception):
    # bc: a resume answer/resolve routed into a ctx.call child was REJECTED by the child's own
    # apply_answer/resolve_in_doubt validation (wrong key, invalid answer, bad verb). At the top
    # level that validation happens BEFORE execute() and raises SystemExit(EXIT_USAGE) — the
    # CLI's normal usage-exit mechanism. Nested, it happens DEEP INSIDE execute() (at the call
    # site applying the claimed ResumeCtx), where a bare SystemExit would escape every library
    # shield and kill the host process. Context.call converts that SystemExit into this, which
    # then propagates BARE through every intermediate Engine.execute() (`except ResumeReject:
    # raise`, the same pattern Corruption uses) so no intermediate parent mislabels it a step
    # failure; only the top-level entry points (run_cli / _execute_shielded) convert it into an
    # ordinary ({"status":"error"}, EXIT_USAGE) result. NOTHING is journaled along the way (no
    # flow_failed, no state write, no call_suspended): a rejected answer consumes nothing, the
    # open call_suspended is untouched, and the gate stays open for a corrected retry.
    pass


class ChildSkew(Exception):
    # bc: a ctx.call child came back EXIT_SKEW — its flow source changed under its embedded
    # journal (a refusal that --accept-flow-change would waive) or its strict-replay guard
    # tripped (NonDeterminism). At the top level BOTH of these error out WITHOUT journaling
    # flow_failed (the run stays resumable once the code is fixed/accepted); a nested child must
    # get the same treatment, not be converted into the parent's permanent step failure. Same
    # bare-propagation pattern as Corruption/ResumeReject: re-raised through every intermediate
    # execute(), converted to ({"status":"error"}, EXIT_SKEW) only at the top-level entry points.
    pass


class KeyCollision(Exception):
    pass


class NonDeterminism(Exception):
    pass


class Corruption(Exception):
    # bc: journal/blob integrity failure (bad JSON mid-file, blob sha mismatch) -> exit 3.
    pass


class FlowError(Exception):
    """Wraps a user-step exception that propagated to the engine top level.

    `step`/`attempts` carry failure provenance (which step, after how many tries) into the
    failed payload/state.json/flow_failed record — additive, absent for glue errors."""
    def __init__(self, name, message, step=None, attempts=None):
        super().__init__(message)
        self.name = name
        self.message = message
        self.step = step
        self.attempts = attempts


# ----------------------------------------------------------------------------- JSON contract
def _assert_json_safe(obj, where):
    """Reject values that can't round-trip identically across Python and JS."""
    if isinstance(obj, bool) or obj is None or isinstance(obj, str):
        return
    if isinstance(obj, int):
        if abs(obj) > MAX_SAFE_INT:
            raise ValueError("%s: integer %d exceeds 2^53-1; carry it as a string" % (where, obj))
        return
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            raise ValueError("%s: NaN/Infinity is not representable in JSON" % where)
        return
    if isinstance(obj, list):
        for i, v in enumerate(obj):
            _assert_json_safe(v, "%s[%d]" % (where, i))
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise ValueError("%s: object key %r is not a string" % (where, k))
            _assert_json_safe(v, "%s.%s" % (where, k))
        return
    raise ValueError("%s: value of type %s is not JSON-safe" % (where, type(obj).__name__))


def _dumps(obj):
    return json.dumps(obj, allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_answer(answer, schema):
    """Light advisory validation of a resolved answer against an ask's schema."""
    if not schema:
        return True, ""
    enum = schema.get("enum")
    if enum is not None and answer not in enum:
        return False, "value not in enum %s" % (enum,)
    t = schema.get("type")
    is_bool = isinstance(answer, bool)
    checks = {
        "boolean": is_bool,
        "string": isinstance(answer, str),
        "number": isinstance(answer, (int, float)) and not is_bool,
        "integer": isinstance(answer, int) and not is_bool,
        "null": answer is None,
        "object": isinstance(answer, dict),
        "array": isinstance(answer, list),
    }
    if t in checks and not checks[t]:
        return False, "expected %s, got %s" % (t, type(answer).__name__)
    return True, ""


def _now_iso():
    # Journal timestamp only — never used for flow control.
    return _time_mod.strftime("%Y-%m-%dT%H:%M:%S", _time_mod.gmtime(_REAL_TIME())) + "Z"


def _usage_exit(msg):
    # bc: the CLI's usage-exit mechanism, with the human message ALSO attached as `.reason` so
    # the nested-resume path (Context.call converting this to ResumeReject) and the library
    # wrappers can surface it in their error payloads instead of a generic "usage error".
    sys.stderr.write(msg + "\n")
    e = SystemExit(EXIT_USAGE)
    e.reason = msg
    raise e


# ----------------------------------------------------------------------------- journal store
class FileStore:
    def __init__(self, state_dir):
        self.dir = state_dir
        self.journal_path = os.path.join(state_dir, "journal.jsonl")
        self.state_path = os.path.join(state_dir, "state.json")
        self.blobs_dir = os.path.join(state_dir, "blobs")
        self.lock_path = os.path.join(state_dir, "lock")
        self._lock_fd = None
        self._seq = 0

    # --- locking -------------------------------------------------------------
    def acquire(self):
        # bc: single-writer lock (flock; engine.js uses an O_EXCL lockfile). Held for one
        # invocation; a second concurrent run exits 13. The holder PID is written into the
        # file so a FOREIGN-language invocation (out of contract: runs resume in their origin
        # language) sees a live holder and fails closed instead of stealing the lock.
        os.makedirs(self.dir, exist_ok=True)
        self._lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EACCES):
                sys.stderr.write("busy: another invocation holds %s\n" % self.lock_path)
                raise SystemExit(EXIT_BUSY)
            raise
        os.ftruncate(self._lock_fd, 0)
        os.write(self._lock_fd, str(os.getpid()).encode("ascii"))

    def release(self):
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
            except OSError:
                pass
            self._lock_fd = None

    # --- reading -------------------------------------------------------------
    def read_records(self):
        if not os.path.exists(self.journal_path):
            return []
        with open(self.journal_path, "rb") as f:
            data = f.read()
        if not data:
            return []
        text = data.decode("utf-8", "strict")
        # bc: torn-tail drop — split on "\n"; the last element is "" (clean end) or a
        # partial line from a torn write. Neither is a durable record, so drop it. The
        # step it would have recorded simply re-runs. (journal-format.md §Crash safety)
        lines = text.split("\n")[:-1]
        records = []
        for i, ln in enumerate(lines):
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except ValueError:
                # bc: a malformed line that is NOT the torn tail = real corruption.
                raise Corruption("journal.jsonl line %d is not valid JSON" % (i + 1))
            if rec.get("v", 1) > SCHEMA_V:
                # bc: a journal written by a newer schema -> refuse rather than misread.
                raise Corruption("journal.jsonl line %d has schema v%s, newer than engine v%d"
                                 % (i + 1, rec.get("v"), SCHEMA_V))
            records.append(rec)
        self._seq = len(records)
        return records

    # --- writing -------------------------------------------------------------
    def _fsync_dir(self):
        try:
            dfd = os.open(self.dir, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass

    def append(self, record):
        record = dict(record)
        record["v"] = SCHEMA_V
        record["seq"] = self._seq
        record["ts"] = _now_iso()
        _assert_json_safe(record, "journal-record")
        line = (_dumps(record) + "\n").encode("utf-8")
        created = not os.path.exists(self.journal_path)
        fd = os.open(self.journal_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            # bc: durability — one record = one write + fsync. The exclusive lock makes
            # this the sole writer, so the macOS ~256B O_APPEND atomic cap can't tear it.
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)
        if created:
            self._fsync_dir()
        self._seq += 1
        return record

    def write_blob(self, key, attempt, value):
        os.makedirs(self.blobs_dir, exist_ok=True)
        name = "%s.%d.json" % (_safe_name(key), attempt)
        path = os.path.join(self.blobs_dir, name)
        payload = _dumps(value).encode("utf-8")
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        self._fsync_dir()
        return name, hashlib.sha256(payload).hexdigest()

    def read_blob(self, ref, sha=None):
        with open(os.path.join(self.blobs_dir, ref), "rb") as f:
            data = f.read()
        if sha is not None and hashlib.sha256(data).hexdigest() != sha:
            # bc: the journal recorded this blob's sha; a mismatch = tampering/truncation.
            raise Corruption("blob %s failed its sha256 integrity check" % ref)
        return json.loads(data)

    def write_state(self, state):
        _assert_json_safe(state, "state")
        tmp = self.state_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(_dumps(state).encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.state_path)
        self._fsync_dir()




class MemoryStore:
    """In-memory, portable Store: same public surface as FileStore (read_records/append/
    write_blob/read_blob/write_state, no-op acquire/release, .dir), backed by a plain list +
    dict instead of a filesystem. Engine/Context/Memo never touch a raw path (verified: every
    call site only ever calls self.store.*), so this is a drop-in swap with ZERO changes to
    those classes. `ctx.call` backs every child with one of these UNCONDITIONALLY, regardless
    of what backs the parent, because a child's state must be embeddable inline in the
    parent's own portable blob (see _portable_state / references/nested-flows.md)."""
    def __init__(self, records=None, blobs=None):
        self.dir = None                 # duck-type parity with FileStore.dir (Context.state_dir)
        self.records = list(records or [])
        self.blobs = dict(blobs or {})  # blob name -> already-parsed JSON value (not bytes)
        self._seq = len(self.records)

    def acquire(self):
        pass

    def release(self):
        pass

    def read_records(self):
        for i, rec in enumerate(self.records):
            if rec.get("v", 1) > SCHEMA_V:
                raise Corruption("record %d has schema v%s, newer than engine v%d"
                                 % (i, rec.get("v"), SCHEMA_V))
        self._seq = len(self.records)
        return list(self.records)       # defensive copy, mirrors FileStore's read-from-disk semantics

    def append(self, record):
        record = dict(record)
        record["v"] = SCHEMA_V
        record["seq"] = self._seq
        record["ts"] = _now_iso()
        _assert_json_safe(record, "journal-record")
        self.records.append(record)
        self._seq += 1
        return record

    def write_blob(self, key, attempt, value):
        _assert_json_safe(value, "blob[%s].%d" % (key, attempt))
        name = "%s.%d.json" % (_safe_name(key), attempt)
        self.blobs[name] = value
        return name, hashlib.sha256(_dumps(value).encode("utf-8")).hexdigest()

    def read_blob(self, ref, sha=None):
        if ref not in self.blobs:
            raise Corruption("blob %r not present in this portable state" % ref)
        value = self.blobs[ref]
        if sha is not None and hashlib.sha256(_dumps(value).encode("utf-8")).hexdigest() != sha:
            # bc: mirrors FileStore.read_blob's integrity check (tampering/truncation of the blob).
            raise Corruption("blob %s failed its sha256 integrity check" % ref)
        return value

    def write_state(self, state):
        _assert_json_safe(state, "state")
        self.state = state


def _safe_name(key):
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in key)


# ----------------------------------------------------------------------------- memo (replay state)
class Memo:
    """Derived view of the journal: what's completed/answered/in-doubt + request order."""
    def __init__(self, store, records):
        self.store = store
        self.completed = {}      # key -> result (newest valid wins)
        self.completed_hash = {} # key -> in_hash recorded with that result (None = unconditional)
        self.answered = {}       # key -> answer
        self.dangling = {}       # key -> attempt (started, no terminal)
        self.resolved = {}       # key -> {action, value} (in-doubt resolution)
        self.attempts = {}       # key -> max attempt seen
        self.key_order = []      # first-request order of keys (steps + asks)
        self.run_id = None
        self.flow_hash = None
        self._build(records)

    def _note_key(self, key):
        if key not in self._seen_order:
            self._seen_order.add(key)
            self.key_order.append(key)

    def _build(self, records):
        # bc: replay state — fold the log into completed{}/answered{}/dangling{} +
        # key_order. step() & ask() read these to decide replay-vs-execute.
        self._seen_order = set()
        started = {}
        for r in records:
            t = r.get("type")
            if t == "run_started":
                if self.run_id is None:
                    self.run_id = r.get("run_id")
                self.flow_hash = r.get("flow_hash")   # latest wins (an accepted flow change moves it)
            elif t == "step_started":
                k = r["key"]
                self._note_key(k)
                started[k] = r["attempt"]
                self.attempts[k] = max(self.attempts.get(k, 0), r["attempt"])
            elif t == "step_completed":
                k = r["key"]
                self._note_key(k)
                if "result_ref" in r:
                    self.completed[k] = self.store.read_blob(r["result_ref"], r.get("result_sha256"))
                else:
                    self.completed[k] = r["result"]
                self.completed_hash[k] = r.get("in_hash")   # newest wins alongside the result
                started.pop(k, None)
            elif t == "step_failed":
                started.pop(r["key"], None)
            elif t == "ask_requested":
                self._note_key(r["key"])
            elif t == "call_suspended":
                # bc: an in-flight, still-open ctx.call must join key_order immediately, exactly
                # like ask_requested does above — otherwise renaming an open call site's key
                # would silently bypass strict-replay instead of raising NonDeterminism (the
                # journal's expected-key check only fires for positions already in key_order).
                self._note_key(r["key"])
            elif t == "ask_answered":
                # bc: _note_key here too — normally a no-op (the gate's ask_requested already
                # noted it at the same position), but the headless interpreter/default path
                # journals a BARE ask_answered with no ask_requested, at exactly the position
                # the gate was reached; without this, that gate is missing from key_order and
                # any LATER resume of the same run trips NonDeterminism at the first request.
                # EXCEPT the synthetic __adjudicate: audit records (handle_step_error) — no flow
                # ever requests those keys, so they must never join key_order.
                if not r["key"].startswith("__adjudicate:"):
                    self._note_key(r["key"])
                self.answered[r["key"]] = r["answer"]
            elif t == "in_doubt_resolved":
                self.resolved[r["key"]] = {"action": r.get("action"), "value": r.get("value")}
            elif t == "flow_changed":
                self.flow_hash = r.get("new_hash")   # an accepted change moves the current hash
            elif t == "memo_invalidated":
                # bc: in-hash invalidation — the walk demanded this key with a DIFFERENT input hash
                # (the definition/rendered input changed), so everything journaled from this key's
                # first occurrence onward is stale HISTORY: drop that tail from key_order so the
                # re-executed walk (which may legitimately diverge here) extends the order afresh.
                # Keyed maps (completed/answered) are NOT dropped — answers survive by decision,
                # and stale completed entries are filtered by the hash check at lookup time.
                k = r["key"]
                if k in self._seen_order:
                    i = self.key_order.index(k)
                    for dk in self.key_order[i:]:
                        self._seen_order.discard(dk)
                    del self.key_order[i:]
        # Anything still "started" with no terminal is in-doubt.
        for k, attempt in started.items():
            self.dangling[k] = attempt


def _find_open_call(records, key):
    """Last `call_suspended` for `key` with no LATER `step_completed` for `key` — i.e. a ctx.call
    still waiting on its child. Mirrors the dangling-step computation Memo._build already does
    for step_started/step_completed, done ad hoc here because Memo deliberately doesn't own
    call_suspended's resolution state (only its key-order membership, see the elif above)."""
    open_rec = None
    for r in records:
        if r.get("type") == "call_suspended" and r.get("key") == key:
            open_rec = r
        elif r.get("type") == "step_completed" and r.get("key") == key:
            open_rec = None
    return open_rec


def _open_call_keys(records):
    """The keys of every ctx.call site in `records` still waiting on a nested child. Same
    raw-record-scanning style as apply_answer/resolve_in_doubt, which already scan records
    directly rather than via Memo."""
    seen = set()
    for r in records:
        if r.get("type") == "call_suspended":
            seen.add(r["key"])
        elif r.get("type") == "step_completed":
            seen.discard(r.get("key"))
    return seen


def _has_open_call(records):
    """True iff SOME ctx.call site anywhere in `records` is still waiting on a nested child —
    i.e. a resume's target is deeper than this level."""
    return bool(_open_call_keys(records))


def _pending_asks(records):
    """The ordered list of ask_requested records with no ask_answered yet — THE canonical
    "which gates are open" fold. Every consumer (apply_answer's target pick, the resume-key
    routing, Context.call's headless intercept, _derive_status) projects from THIS list, so the
    latest-open-gate semantics can never drift between them."""
    answered = {r["key"] for r in records if r.get("type") == "ask_answered"}
    return [r for r in records if r.get("type") == "ask_requested" and r["key"] not in answered]


def _pending_ask_keys(records):
    """Set projection of _pending_asks, for membership checks in the resume-key routing."""
    return {r["key"] for r in _pending_asks(records)}


def _dangling_step_keys(records):
    """Keys of step_started records with no terminal — the raw-scan equivalent of Memo.dangling,
    used (like _pending_ask_keys) to decide whether an explicit resume key targets THIS level."""
    open_steps = {}
    for r in records:
        t = r.get("type")
        if t == "step_started":
            open_steps[r["key"]] = True
        elif t in ("step_completed", "step_failed"):
            open_steps.pop(r.get("key"), None)
    return set(open_steps)


def _strip_call_prefix(k, call_key):
    """Peel ONE call-site prefix off a hoisted resume key: "child/gate" arriving at the ctx.call
    whose key is "child" becomes "gate" for the child level. Exact-prefix only — a key that
    doesn't start with `call_key + "/"` passes through unchanged (the bare-leaf form, which was
    the only working form before path addressing existed, and still must win at each level's own
    local-exact-match check first — see the precedence comment in Context.call)."""
    if k is not None and k.startswith(call_key + "/"):
        return k[len(call_key) + 1:]
    return k


# ----------------------------------------------------------------------------- flow + context
class Flow:
    def __init__(self, fn, fid, version):
        self.fn = fn
        self.id = fid
        self.version = version
        self._resumable_flow = True   # duck-typed marker (survives double-import)

    def __call__(self, ctx, inp):
        return self.fn(ctx, inp)


def flow(id, version=1):
    def deco(fn):
        return Flow(fn, id, version)
    return deco


class Context:
    def __init__(self, engine):
        self._e = engine
        self._seen = set()          # keys requested this pass (collision guard)
        self._req_index = 0         # position in the request stream (strict-replay)
        self._auto = {}             # counters for now/random/uuid sugar

    # --- internal: every step/ask funnels through here -----------------------
    def _request(self, key):
        # bc: guards on every step/ask — duplicate key in one pass = exit 2; replayed
        # key sequence must extend the journal's order, else exit 3 (drift caught loud).
        if key in self._seen:
            raise KeyCollision("duplicate step/ask key in one pass: %r" % key)
        self._seen.add(key)
        expected = self._e.memo.key_order
        i = self._req_index
        if self._e.strict and i < len(expected) and expected[i] != key:
            raise NonDeterminism(
                "replay divergence at request #%d: journal expected %r, flow requested %r"
                % (i, expected[i], key))
        self._req_index += 1

    # --- public API ----------------------------------------------------------
    @property
    def state_dir(self):
        # bc: the run's on-disk state directory (journal/blobs/state.json live here). The
        # workflow `agent` kind points its state MCP server at this dir.
        return self._e.store.dir

    def step(self, key, fn, idempotent=True, retries=0, backoff_ms=0, desc=None, on_fail=None,
             in_hash=None):
        self._request(key)
        memo = self._e.memo
        if key in memo.completed:
            if memo.completed_hash.get(key) == in_hash:
                self._e._observe({"phase": "replay", "key": key, "result": memo.completed[key], "desc": desc})
                return memo.completed[key]           # bc: REPLAY — recorded result, fn NOT run
            # bc: IN-HASH INVALIDATION — the memoized result was produced by a DIFFERENT input
            # (edited prompt / changed upstream rendering). Journal the marker (it truncates the
            # stale key_order tail on every future fold — the walk may legitimately diverge from
            # here), drop the stale entry, and fall through to re-execute. Newest valid wins.
            self._e.store.append({"type": "memo_invalidated", "key": key,
                                  "old_hash": memo.completed_hash.get(key), "new_hash": in_hash})
            self._e._observe({"phase": "invalidated", "key": key,
                              "old_hash": memo.completed_hash.get(key), "new_hash": in_hash})
            if key in memo.key_order:            # an earlier invalidation this pass may have
                i = memo.key_order.index(key)    # already truncated past this key
                del memo.key_order[i:]
            memo.completed.pop(key, None)
            memo.completed_hash.pop(key, None)
        if key in memo.dangling and not idempotent:
            # bc: in-doubt — non-idempotent step interrupted. If the orchestrator resolved it
            # (resume --resolve), apply that; otherwise escalate (exit 11).
            res = memo.resolved.get(key)
            if res is None:
                return self._e.handle_in_doubt(key, memo.dangling[key])
            if res["action"] == "completed":
                return self._e._complete(key, res.get("value"))
            if res["action"] == "abort":
                raise FlowError("aborted", "in-doubt step %s aborted by resolution" % key, step=key)
            # res["action"] == "retry" -> fall through and re-execute the step once
        # bc: idem key = run_id:key — forward to the side effect so the downstream dedupes
        # a crash-window re-run (run_id is stable across resumes; see Engine.execute).
        idem_key = "%s:%s" % (self._e.run_id, key)
        base_attempt = memo.attempts.get(key, 0)
        attempt = base_attempt + 1
        while True:
            # bc: EXECUTE — started -> fn -> completed. A throw writes step_failed and is
            # NEVER memoized, so the step re-attempts on the next run (your "re-attempt").
            started = {"type": "step_started", "key": key,
                       "attempt": attempt, "idempotency_key": idem_key}
            if desc is not None:                       # bc: self-describing journal (workflow `intent`)
                started["desc"] = desc
            self._e.store.append(started)
            self._e._observe({"phase": "before", "key": key, "attempt": attempt,
                              "idempotency_key": idem_key, "desc": desc})
            try:
                result = _invoke(fn, idem_key)
            except Exception as e:  # noqa: BLE001 - we journal & decide
                err = {"name": type(e).__name__, "message": str(e)}
                # bc: on_fail — per-attempt failure policy hook (compiled from workflow `on_error`
                # rules). Consulted with (error, attempt); returns {"action": retry|catch|raise,
                # "backoff_ms"?}. Supersedes the retries/backoff_ms counters when provided. A
                # broken policy must never mask the step error -> treated as "raise".
                decision = None
                if on_fail is not None:
                    try:
                        decision = on_fail(dict(err), attempt)
                    except Exception:  # noqa: BLE001
                        decision = None
                if on_fail is not None:
                    will_retry = isinstance(decision, dict) and decision.get("action") == "retry"
                else:
                    will_retry = attempt <= retries
                self._e.store.append({"type": "step_failed", "key": key, "attempt": attempt,
                                      "error": {"name": err["name"], "message": err["message"],
                                                "retriable": will_retry}})
                self._e._observe({"phase": "failed", "key": key, "attempt": attempt, "error": str(e)})
                if will_retry:
                    bo = decision.get("backoff_ms", 0) if on_fail is not None else backoff_ms
                    if bo:
                        # bc: in-process backoff is capped LOW — a long wait inside a held lock
                        # rebuilds the waiting process suspend-by-exit exists to eliminate.
                        # Model longer waits as gates (durable timers are a future record type).
                        _time_mod.sleep(min((bo / 1000.0) * (2 ** (attempt - 1)), 60.0))
                    attempt += 1
                    continue
                if isinstance(decision, dict) and decision.get("action") == "catch":
                    # bc: CATCH — memoize the failure as a synthesized step_completed (error
                    # sentinel), so replay deterministically re-takes the same failure branch
                    # even if the underlying issue is later fixed. Adjudicator not consulted.
                    sentinel = {"__error__": {"name": err["name"], "message": err["message"],
                                              "attempts": attempt}}
                    return self._e._complete(key, sentinel, attempt=attempt, in_hash=in_hash)
                return self._e.handle_step_error(key, type(e).__name__, str(e), attempt=attempt)
            _assert_json_safe(result, "step[%s].result" % key)
            rec = {"type": "step_completed", "key": key, "attempt": attempt}
            if in_hash is not None:
                rec["in_hash"] = in_hash
            if len(_dumps(result).encode("utf-8")) > BLOB_THRESHOLD:
                ref, sha = self._e.store.write_blob(key, attempt, result)
                rec["result_ref"] = ref
                rec["result_sha256"] = sha
            else:
                rec["result"] = result
            self._e.store.append(rec)
            memo.completed[key] = result
            self._e._observe({"phase": "after", "key": key, "attempt": attempt,
                              "result": result, "desc": desc})
            return result

    def ask(self, key, question, schema=None, desc=None):
        self._request(key)
        memo = self._e.memo
        if key in memo.answered:
            self._e._observe({"phase": "replay", "key": key, "result": memo.answered[key], "desc": desc})
            return memo.answered[key]                 # bc: REPLAY — answer already in journal
        if self._e.headless:
            return self._e.auto_answer(key, question, schema)
        # bc: SUSPEND — record the question, raise out of the flow. The process exits 10;
        # the answer arrives later via `resume --answer` and is present on the next replay.
        req = {"type": "ask_requested", "key": key, "question": question, "schema": schema}
        if desc is not None:
            req["desc"] = desc
        self._e.store.append(req)
        self._e._observe({"phase": "ask", "key": key, "question": question, "desc": desc})
        raise Suspend(key, question, schema)

    # wait() is the general durable gate; ask() is the human-facing alias.
    def wait(self, key, question=None, schema=None, desc=None):
        return self.ask(key, question or {"prompt": "waiting for %s" % key}, schema, desc=desc)

    def call(self, key, child_flow, child_input, desc=None, on_fail=None):
        # bc: an independent, reusable CHILD flow — not just a namespaced helper sharing this
        # flow's journal (that's what map/_do_model_step already do). The child is ALWAYS backed
        # by a MemoryStore (regardless of what backs the parent) so its state is embeddable
        # inline in the parent's own portable blob. If the child suspends/goes in-doubt, THIS
        # call raises ChildSuspend/ChildInDoubt, which Engine.execute() converts into an
        # identically-shaped suspended/in_doubt payload for the current level — composing to
        # arbitrary depth via ordinary exception propagation, no manual tree-walk anywhere.
        self._request(key)
        memo = self._e.memo
        if key in memo.completed:
            self._e._observe({"phase": "replay", "key": key, "result": memo.completed[key], "desc": desc})
            return memo.completed[key]               # bc: REPLAY — zero child machinery touched

        open_rec = _find_open_call(self._e.store.read_records(), key)
        # bc: observer "call" phase — a call boundary is being crossed live (not replayed);
        # `resumed` distinguishes a fresh child from one reconstituted mid-suspend. Additive to
        # the existing before/after/replay vocabulary; child-internal events flow through the
        # SAME shared observer (inherited below) with their child-local keys.
        self._e._observe({"phase": "call", "key": key, "flow_id": child_flow.id,
                          "resumed": open_rec is not None, "desc": desc})
        if open_rec is not None:
            cs = open_rec["child_state"]
            child_store = MemoryStore(records=cs["records"], blobs=cs.get("blobs", {}))
        else:
            child_store = MemoryStore()
        child_engine = Engine(child_flow, child_store, strict=self._e.strict, headless=self._e.headless,
                              interpreter=self._e.interpreter, adjudicator=self._e.adjudicator,
                              observer=self._e.observer,
                              accept_flow_change=self._e.accept_flow_change)

        # bc: the resume-answer token is ONE-SHOT and self-discovering — see references/
        # nested-flows.md "The recursive resume algorithm". A call site claims it only if it is
        # BOTH currently open (open_rec is not None) AND the parent still holds the token
        # (rc is not None); any earlier call site in this pass already returned above at the
        # memo.completed check, so it never reaches this line and can never mis-claim it.
        rc = self._e._resume_ctx
        if rc is not None and open_rec is not None:
            self._e._resume_ctx = None                      # claimed HERE; no other site can claim it
            self._apply_resume_token(rc, key, child_store, child_engine)

        input_value = _input_from_journal(child_store) if open_rec is not None else child_input
        try:
            payload, code = child_engine.execute(input_value)
        except SystemExit as se:
            if se.code != EXIT_NO_AUTOANSWER:
                raise
            # bc: a HEADLESS child hit a gate it cannot auto-answer. Context.ask's own headless
            # branch (auto_answer) already durably appended the child's ask_requested before
            # raising this, so child_store has it — embed it as a call_suspended (same as an
            # ordinary suspend) so a later answer, or a non-headless resume, still works, then
            # re-raise the SAME signal at this level: the parent is equally headless and equally
            # cannot resolve it either. Without this, the SystemExit would otherwise escape
            # straight through this call with NOTHING journaled at all, silently discarding
            # every step the child had already completed.
            pending = _pending_asks(child_store.read_records())
            p = pending[-1] if pending else {}
            needs_answer = {"status": "needs_answer",
                            "pending": {"key": p.get("key"), "question": p.get("question"),
                                       "schema": p.get("schema")}}
            state_blob = _portable_state(child_engine, needs_answer)
            self._e.store.append({"type": "call_suspended", "key": key, "child_state": state_blob})
            self._e._observe({"phase": "call_suspended", "key": key,
                              "pending": needs_answer["pending"],
                              "in_doubt": False, "blocked": True, "desc": desc})
            raise
        except Corruption:
            # bc: a corrupt blob discovered while building the child's OWN Memo happens before
            # child_engine.execute()'s try block even starts — it escapes as a bare exception,
            # exactly like a top-level Corruption escapes execute() to whoever calls it
            # (run_cli/run_flow/resume_flow's own `except Corruption -> EXIT_SKEW`). Re-raise
            # bare (not FlowError) so it propagates the SAME way through however many ctx.call
            # levels sit above, reaching that SAME top-level handling — instead of being
            # downgraded to an ordinary flow_failed by an intervening parent's generic catch.
            raise

        return self._handle_child_result(key, child_engine, payload, code, desc, on_fail)

    def _apply_resume_token(self, rc, key, child_store, child_engine):
        # bc: path-aware key addressing, rules 2+3 (rule 1 — local-exact-match wins — already
        # ran at _prepare_resume for the top level, and runs below for THIS child level).
        # A hoisted key like "child/gate" loses this site's own "child/" prefix on the way
        # down, so the value pending.key SHOWS the caller round-trips verbatim; a bare leaf
        # key passes through _strip_call_prefix unchanged (backward compat).
        rc.answer_key = _strip_call_prefix(rc.answer_key, key)
        rc.resolve_key = _strip_call_prefix(rc.resolve_key, key)
        explicit_key = rc.resolve_key if rc.resolve is not None else rc.answer_key
        local = (_dangling_step_keys(child_store.records) if rc.resolve is not None
                 else _pending_ask_keys(child_store.records))
        local_hit = explicit_key is not None and explicit_key in local
        try:
            if _has_open_call(child_store.records) and not local_hit:
                child_engine._resume_ctx = rc            # the real gate is deeper — hand the token down
            elif rc.resolve is not None:
                child_engine.resolve_in_doubt(rc.resolve, rc.resolve_key, rc.resolve_value)
            else:
                child_engine.apply_answer(rc.raw_answer, rc.answer_key)
        except SystemExit as se:
            if se.code != EXIT_USAGE:
                raise
            # bc: the child REJECTED the answer/resolve (wrong key, invalid value). At the top
            # level this same rejection happens before execute() and exits cleanly; from HERE
            # a bare SystemExit would blow through every library shield — convert it to
            # ResumeReject, which propagates journal-free to the top-level entry points
            # (nothing consumed, gate stays open). See the ResumeReject class comment.
            raise ResumeReject(getattr(se, "reason", "resume rejected at %r" % key))

    def _handle_child_result(self, key, child_engine, payload, code, desc, on_fail):
        if code in (EXIT_SUSPENDED, EXIT_IN_DOUBT):
            state_blob = _portable_state(child_engine, payload)
            self._e.store.append({"type": "call_suspended", "key": key, "child_state": state_blob})
            self._e._observe({"phase": "call_suspended", "key": key,
                              "pending": payload["pending"],
                              "in_doubt": code == EXIT_IN_DOUBT, "desc": desc})
            exc = ChildSuspend if code == EXIT_SUSPENDED else ChildInDoubt
            raise exc(key, state_blob, payload["pending"])
        if code == EXIT_OK:
            return self._e._complete(key, payload["result"])
        if code == EXIT_SKEW:
            # bc: the child refused a flow-source change (or its strict-replay guard tripped) —
            # at the top level these error out WITHOUT consuming the run; propagate the same way
            # (see ChildSkew class comment) instead of misreporting a permanent step failure.
            err = payload.get("error")
            msg = err if isinstance(err, str) else _dumps(err)
            raise ChildSkew("child %r: %s" % (key, msg))
        # bc: anything else (EXIT_FLOW_FAILED's {name,message,...} OR EXIT_USAGE's bare-string
        # {"error": "..."} from a KeyCollision — a hard bug in the child's own code) — surface
        # uniformly as an ordinary step failure. on_fail (if given) is consulted FIRST and its
        # "catch" memoizes the step-style error sentinel, so replay deterministically re-takes
        # the same failure branch (identical semantics to ctx.step's on_fail catch); otherwise
        # the PARENT's adjudicator gets it via Engine.handle_step_error, so failure policy
        # composes across a ctx.call boundary instead of stopping dead at it. There is
        # DELIBERATELY no "retry" for a call: a failed child persists nothing, so a retry would
        # re-run the ENTIRE child from scratch, re-firing every side effect with zero partial
        # credit — the exact hazard the in-doubt machinery exists to prevent. Re-invoke the
        # parent instead (the memoized prefix replays for free) after fixing the cause.
        err = payload.get("error")
        if isinstance(err, dict):
            name, message = err.get("name", "Error"), err.get("message", "")
        else:
            name, message = "Error", str(err)
        if on_fail is not None:
            try:
                decision = on_fail({"name": name, "message": message}, 1)
            except Exception:  # noqa: BLE001 - a broken policy must never mask the child error
                decision = None
            if isinstance(decision, dict) and decision.get("action") == "catch":
                sentinel = {"__error__": {"name": name, "message": message, "attempts": 1}}
                return self._e._complete(key, sentinel)
        return self._e.handle_step_error(key, name, message)

    def now(self):
        n = self._auto.get("now", 0)
        self._auto["now"] = n + 1
        return self.step("__now:%d" % n, lambda: _REAL_TIME())

    def random(self):
        n = self._auto.get("rand", 0)
        self._auto["rand"] = n + 1
        return self.step("__rand:%d" % n, lambda: _REAL_RANDOM())

    def uuid(self):
        n = self._auto.get("uuid", 0)
        self._auto["uuid"] = n + 1
        return self.step("__uuid:%d" % n, lambda: str(_REAL_UUID()))


def _invoke(fn, idem_key):
    # Only inject the idempotency key into a parameter *explicitly* named idem_key/idem (by
    # keyword), so the common `lambda x=x:` loop-capture idiom is never clobbered. NOTE: engine.js
    # always passes idemKey as arg0 (JS has no default-arg capture idiom, and ignores extra args) —
    # the py-by-name / js-by-position asymmetry is INTENTIONAL, not a bug.
    try:
        params = inspect.signature(fn).parameters
    except (ValueError, TypeError):
        params = {}
    for name in ("idem_key", "idem"):
        if name in params:
            return fn(**{name: idem_key})
    return fn()


class ResumeCtx:
    """A one-shot resume-answer token threaded through a chain of ctx.call frames. Carries NO
    separate path/depth field — each Context.call independently discovers whether ITS open call
    is the target (apply here) or the target is deeper (hand `self` down to the child engine's
    own `_resume_ctx`). The optional answer_key/resolve_key ARE path-aware: a hoisted key like
    "child/leaf/gate" has each claiming call site's own prefix stripped on the way down
    (_strip_call_prefix), so the exact pending.key the API surfaced round-trips verbatim, while
    bare leaf-local keys pass through unchanged. See Context.call and references/nested-flows.md."""
    def __init__(self, raw_answer=None, answer_key=None,
                resolve=None, resolve_key=None, resolve_value=None):
        self.raw_answer = raw_answer      # a raw string — apply_answer does its own JSON parsing
        self.answer_key = answer_key
        self.resolve = resolve
        self.resolve_key = resolve_key
        self.resolve_value = resolve_value


def _prepare_resume(engine, store, raw_answer=None, answer_key=None,
                    resolve=None, resolve_key=None, resolve_value=None):
    """Shared resume-preparation logic for run_cli and resume_flow: either seed the one-shot
    ResumeCtx (the open gate is inside a nested ctx.call, possibly several levels deep) or apply
    the answer/resolve directly at THIS level, exactly as before ctx.call existed. Returns None
    on success, or a usage-error message string (apply_answer/resolve_in_doubt's OWN validation
    failures still raise SystemExit(EXIT_USAGE) directly, unchanged, and propagate through).

    Key-addressing precedence (rule 1 of 3 — the other two live in Context.call): an explicit
    key that EXACTLY matches a locally-open gate/dangling step is applied at THIS level even if
    an open ctx.call also exists — "/" is legal inside plain step keys (map builds scan#0/map#3-
    style keys), so a local exact match must always beat path interpretation."""
    records = store.read_records()
    explicit_key = resolve_key if resolve is not None else answer_key
    local = (_dangling_step_keys(records) if resolve is not None else _pending_ask_keys(records))
    if _has_open_call(records) and not (explicit_key is not None and explicit_key in local):
        if resolve is None and raw_answer is None:
            return "resume requires --answer or --resolve"
        engine._resume_ctx = ResumeCtx(raw_answer=raw_answer, answer_key=answer_key,
                                       resolve=resolve, resolve_key=resolve_key,
                                       resolve_value=resolve_value)
    elif resolve is not None:
        engine.resolve_in_doubt(resolve, resolve_key, resolve_value)
    elif raw_answer is not None:
        engine.apply_answer(raw_answer, answer_key)
    else:
        return "resume requires --answer or --resolve"
    return None


# ----------------------------------------------------------------------------- engine
class Engine:
    def __init__(self, flow_obj, store, strict=True, headless=False,
                 interpreter=None, adjudicator=None, observer=None, accept_flow_change=False):
        self.flow = flow_obj
        self.store = store
        self.strict = strict
        self.headless = headless
        self.accept_flow_change = accept_flow_change
        self.interpreter = interpreter
        self.adjudicator = adjudicator
        self.observer = observer
        self.memo = None
        self.run_id = None
        self._resume_ctx = None   # bc: one-shot ResumeCtx token — see Context.call

    def _observe(self, event):
        # bc: out-of-band "thinking"/progress hook. NOT journaled, runs on EVERY pass (incl.
        # replay), and is try-guarded so an observer error can never affect the flow.
        if self.observer is None:
            return
        try:
            self.observer(event)
        except Exception:  # noqa: BLE001
            pass

    def flow_hash(self):
        try:
            src = inspect.getsource(self.flow.fn).encode("utf-8")
        except (OSError, TypeError):
            src = b""
        return "sha256:" + hashlib.sha256(src).hexdigest()

    # --- in-doubt / error policy --------------------------------------------
    def handle_in_doubt(self, key, attempt):
        # Non-idempotent step interrupted mid-flight (started, no terminal): escalate
        # (exit 11) for external adjudication rather than risk a blind re-apply.
        raise InDoubt(key, attempt)

    def handle_step_error(self, key, name, message, attempt=None):
        # adjudicator (if any) may resolve a failed step: skip -> return a value, or
        # abort. Any other / no decision -> propagate the original failure. `name`/`message`
        # rather than a raw exception object so Context.call can reuse this for a failed ctx.call
        # child (whose failure only ever exists as {name,message} extracted from a child payload,
        # never a live Python exception) — composing adjudicator policy across a call boundary.
        if self.adjudicator is not None:
            decision = self.adjudicator({"kind": "step_failed", "key": key, "error": message})
            self.store.append({"type": "ask_answered", "key": "__adjudicate:%s" % key,
                               "raw": _dumps(decision), "answer": decision,
                               "interpreted_by": "llm"})
            if decision.get("action") == "skip":
                # bc: MEMOIZE the skip value (step_completed) so resume does NOT re-run the
                # step and re-invoke the adjudicator during replay.
                return self._complete(key, decision.get("value"))
            if decision.get("action") == "abort":
                raise FlowError("aborted", "adjudicator aborted at %s" % key, step=key)
        raise FlowError(name, message, step=key, attempts=attempt)

    def _complete(self, key, value, attempt=None, in_hash=None):
        # Journal a step_completed (spilling to a blob if large) and memoize it.
        _assert_json_safe(value, "step[%s].result" % key)
        if attempt is None:
            attempt = self.memo.attempts.get(key, 1)
        rec = {"type": "step_completed", "key": key, "attempt": attempt}
        if in_hash is not None:
            rec["in_hash"] = in_hash
        if len(_dumps(value).encode("utf-8")) > BLOB_THRESHOLD:
            ref, sha = self.store.write_blob(key, attempt, value)
            rec["result_ref"] = ref
            rec["result_sha256"] = sha
        else:
            rec["result"] = value
        self.store.append(rec)
        self.memo.completed[key] = value
        self.memo.completed_hash[key] = in_hash
        self._observe({"phase": "after", "key": key, "attempt": attempt, "result": value,
                       "synthesized": True})
        return value

    def auto_answer(self, key, question, schema):
        # bc: the can't-answer paths journal the durable ask_requested here but do NOT _emit —
        # the SystemExit's `.reason` carries the message and the single needs_answer payload is
        # emitted once at the TOP level (run_cli/_execute_shielded), where _derive_status hoists
        # the key/chain correctly for nested ctx.call flows and a library caller's stdout stays
        # clean. Emitting from HERE printed the leaf-local key at whatever depth this gate lives.
        if schema and "default" in schema:
            ans = schema["default"]
            by = "default"
        elif self.interpreter is not None:
            ans = self.interpreter({"key": key, "question": question, "schema": schema, "raw": None})
            by = "llm"
        else:
            self.store.append({"type": "ask_requested", "key": key,
                               "question": question, "schema": schema})
            raise SystemExit(EXIT_NO_AUTOANSWER)
        # bc: headless answers pass the SAME gate as --answer — an invalid default/interpreter
        # reply must not be memoized forever. Reject (don't journal the answer), leave the gate open.
        ok, why = _validate_answer(ans, schema)
        if not ok:
            sys.stderr.write("auto-answer rejected for %r: %s\n" % (key, why))
            self.store.append({"type": "ask_requested", "key": key,
                               "question": question, "schema": schema})
            e = SystemExit(EXIT_NO_AUTOANSWER)
            e.reason = "auto-answer rejected: %s" % why
            raise e
        self.store.append({"type": "ask_answered", "key": key, "raw": None,
                           "answer": ans, "interpreted_by": by})
        self.memo.answered[key] = ans
        return ans

    def resolve_in_doubt(self, action, key=None, value=None):
        """Record an orchestrator's resolution of an in-doubt step (resume --resolve)."""
        if action not in ("completed", "retry", "abort"):
            _usage_exit("resolve: --resolve must be completed|retry|abort")
        records = self.store.read_records()
        started = {}
        for r in records:
            t = r.get("type")
            if t == "step_started":
                started[r["key"]] = True
            elif t in ("step_completed", "step_failed"):
                started.pop(r["key"], None)
        dangling = list(started.keys())
        if key is None:
            if len(dangling) == 1:
                key = dangling[0]
            elif not dangling:
                _usage_exit("resolve: no in-doubt step to resolve")
            else:
                _usage_exit("resolve: multiple in-doubt steps %s; use --resolve-key" % dangling)
        elif key not in dangling:
            _usage_exit("resolve: --resolve-key %r is not an in-doubt step" % key)
        rec = {"type": "in_doubt_resolved", "key": key, "action": action}
        if action == "completed":
            rec["value"] = value
        self.store.append(rec)

    def apply_answer(self, raw, key=None):
        """Append an ask_answered for the pending gate, interpreting free-form input."""
        # bc: resume step 1 of 2 — record the answer, THEN execute() re-runs the flow; the
        # gate that suspended now finds its answer in the journal and returns it.
        question = None
        schema = None
        pending = [(r["key"], r.get("question"), r.get("schema"))
                   for r in _pending_asks(self.store.read_records())]
        if key is None:
            if pending:
                key, question, schema = pending[-1]   # latest still-open gate
        else:
            # bc: an explicit --key must name a currently-open gate, else reject (no orphan).
            match = [(k, q, s) for (k, q, s) in pending if k == key]
            if not match:
                _usage_exit("resume: --key %r is not an open gate" % key)
            key, question, schema = match[0]
        if key is None:
            _usage_exit("resume: no pending ask to answer")
        try:
            answer = json.loads(raw)
            valid_json = True
        except (ValueError, TypeError):
            answer, valid_json = None, False
        if not valid_json and self.interpreter is not None:
            answer = self.interpreter({"key": key, "question": question,
                                       "schema": schema, "raw": raw})
            by = "llm"
        elif valid_json:
            by = "human"
        else:
            answer, by = raw, "human"
        ok, why = _validate_answer(answer, schema)
        if not ok:
            # bc: reject (don't journal) so the gate stays open for a corrected answer.
            _usage_exit("answer rejected for %r: %s" % (key, why))
        self.store.append({"type": "ask_answered", "key": key, "raw": raw,
                           "answer": answer, "interpreted_by": by})

    # --- main execution ------------------------------------------------------
    def check_flow_change(self):
        """Refuse (exit-3 payload) when the flow fn's source changed under a live journal —
        editing a step BODY while keeping its key would replay old results against new code,
        the one drift the strict key-sequence guard cannot see. --accept-flow-change proceeds
        and journals the acceptance. MUST run before ANY journal mutation: run_cli calls it
        ahead of apply_answer/resolve_in_doubt (a refused resume must not consume the answer);
        execute() re-checks for the plain `run` path (no-op after an acceptance)."""
        prior = None
        records = self.store.read_records()
        for r in records:
            t = r.get("type")
            if t == "run_started":
                prior = r.get("flow_hash")
            elif t == "flow_changed":
                prior = r.get("new_hash")
        fhash = self.flow_hash()
        if prior and prior != fhash:
            if not self.accept_flow_change:
                msg = ("flow_hash changed since suspend; re-invoke with --accept-flow-change "
                       "to proceed (journals a flow_changed record)")
                sys.stderr.write("FlowChanged: %s\n" % msg)
                return {"status": "error", "error": msg}
            open_calls = sorted(_open_call_keys(records))
            if open_calls:
                # bc: BIGGER blast radius than an ordinary key rename — each of these keys holds
                # an entire embedded child sub-journal (possibly with its own open human gate);
                # if the new code renames/repurposes one, that whole child state is orphaned.
                # Warn loudly; never refuse (the flag is explicit user intent).
                sys.stderr.write("warning: accepting flow change with open nested call(s) %s; "
                                 "their embedded child state will replay under the new code\n"
                                 % ", ".join(repr(k) for k in open_calls))
            self.store.append({"type": "flow_changed", "old_hash": prior, "new_hash": fhash})
        return None

    def execute(self, input_value):
        err = self.check_flow_change()
        if err:
            return err, EXIT_SKEW
        records = self.store.read_records()
        self.memo = Memo(self.store, records)
        fhash = self.flow_hash()
        # bc: run_id is minted once (first run) and reused on every resume — it roots the
        # idempotency key, so it must NOT change across invocations.
        if self.memo.run_id is None:
            self.run_id = _REAL_UUID().hex
        else:
            self.run_id = self.memo.run_id
        self.store.append({"type": "run_started", "run_id": self.run_id,
                           "flow_id": self.flow.id, "flow_version": self.flow.version,
                           "flow_hash": fhash, "engine": "py",
                           "engine_version": "1.0.0", "input": input_value})
        ctx = Context(self)
        # bc: resume protocol — re-run the fn from the top; the exception that escapes maps
        # to the exit code below. A clean return is success.
        try:
            result = self.flow(ctx, input_value)
        except Suspend as s:
            self.store.append({"type": "flow_suspended", "pending_key": s.key})
            self._state("suspended", pending={"key": s.key, "question": s.question,
                                              "schema": s.schema})
            return {"status": "suspended",
                    "pending": {"key": s.key, "question": s.question, "schema": s.schema}}, EXIT_SUSPENDED
        except InDoubt as d:
            # bc: options are exactly the CLI `--resolve` verbs; state.json mirrors stdout.
            pending = {"key": d.key, "attempt": d.attempt, "interrupted_step": d.key,
                       "options": ["completed", "retry", "abort"]}
            self._state("in_doubt", pending=pending)
            return {"status": "in_doubt", "pending": pending}, EXIT_IN_DOUBT
        except ChildSuspend as cs:
            # bc: a ctx.call child suspended. Context.call already appended the ONE
            # call_suspended record before raising — nothing more to journal here. Composes to
            # arbitrary depth: if THIS level is itself a ctx.call child, this return value is
            # exactly what the PARENT's Context.call sees, becoming ITS OWN ChildSuspend.
            pending = _hoist_pending(cs.key, cs.child_pending)
            self._state("suspended", pending=pending)
            return {"status": "suspended", "pending": pending}, EXIT_SUSPENDED
        except ChildInDoubt as cd:
            pending = _hoist_pending(cd.key, cd.child_pending, in_doubt=True)
            self._state("in_doubt", pending=pending)
            return {"status": "in_doubt", "pending": pending}, EXIT_IN_DOUBT
        except KeyCollision as e:
            sys.stderr.write("KeyCollision: %s\n" % e)
            return {"status": "error", "error": str(e)}, EXIT_USAGE
        except NonDeterminism as e:
            sys.stderr.write("NonDeterminism: %s\n" % e)
            return {"status": "error", "error": str(e)}, EXIT_SKEW
        except FlowError as e:
            return self._fail(e.name, e.message, step=e.step, attempts=e.attempts)
        except Corruption:
            # bc: a nested ctx.call child's OWN Memo construction can raise Corruption deep
            # inside self.flow(...) (Context.call re-raises it bare, engine.py ~665) — propagate
            # it the SAME way a top-level Corruption already does (escaping execute() entirely,
            # before this try block even starts, for THIS level's own Memo). Re-raising here
            # (instead of falling into the generic catch below) means it threads through however
            # many ctx.call levels sit above, reaching run_cli/run_flow/resume_flow's existing
            # `except Corruption -> EXIT_SKEW` at the true top, instead of being downgraded to an
            # ordinary flow_failed (EXIT_FLOW_FAILED) by an intervening parent.
            raise
        except (ResumeReject, ChildSkew):
            # bc: same propagation pattern as Corruption above — a rejected nested resume answer
            # (ResumeReject) or a nested flow-change refusal / replay divergence (ChildSkew) must
            # be a JOURNAL-FREE no-op at every level (no flow_failed, no state write, gate stays
            # open); only the top-level entry points (run_cli/_execute_shielded) convert them
            # into ({"status":"error"}, EXIT_USAGE/EXIT_SKEW) results. See the class comments.
            raise
        except Exception as e:  # noqa: BLE001
            # bc: glue/result error (not a step throw, not a signal) -> clean failed, not a crash.
            return self._fail(type(e).__name__, str(e))
        try:
            _assert_json_safe(result, "flow.result")
        except ValueError as e:
            return self._fail("ValueError", str(e))
        self.store.append({"type": "flow_completed", "result": result})
        self._state("completed", result=result)
        return {"status": "completed", "result": result}, EXIT_OK

    def _fail(self, name, message, step=None, attempts=None):
        # bc: failure provenance — step/attempts present only when the failure came from a step
        # (glue failures keep the bare {name,message} shape). Additive within journal schema v1.
        error = {"name": name, "message": message}
        if step is not None:
            error["step"] = step
        if attempts is not None:
            error["attempts"] = attempts
        self.store.append({"type": "flow_failed", "error": error})
        self._state("failed", error=error)
        return {"status": "failed", "error": error}, EXIT_FLOW_FAILED

    def _state_payload(self, status, pending=None, result=None, error=None):
        return {"v": SCHEMA_V, "flow_id": self.flow.id, "flow_version": self.flow.version,
                "flow_hash": self.flow_hash(), "run_id": self.run_id, "status": status,
                "pending": pending, "result": result, "error": error,
                "engine": "py", "engine_version": "1.0.0"}

    def _state(self, status, pending=None, result=None, error=None):
        self.store.write_state(self._state_payload(status, pending, result, error))


def _hoist_pending(local_key, child_pending, in_doubt=False):
    # bc: builds the "/"-joined key + chain breadcrumb for a suspended/in-doubt ctx.call,
    # recursively composable — child_pending may itself already carry a multi-segment "chain"
    # from a grandchild, in which case it's just extended, not replaced.
    leaf_path = child_pending["key"]
    out = {"key": "%s/%s" % (local_key, leaf_path)}
    if in_doubt:
        out["attempt"] = child_pending.get("attempt")
        out["interrupted_step"] = out["key"]
        out["options"] = child_pending.get("options", ["completed", "retry", "abort"])
    else:
        out["question"] = child_pending.get("question")
        out["schema"] = child_pending.get("schema")
    out["chain"] = [local_key] + (child_pending.get("chain") or [leaf_path])
    return out


def _portable_state(engine, payload):
    # bc: the exporter implementing references/nested-flows.md's portable-state schema — the
    # WHOLE resumable state as one self-contained JSON value: every journal record for this
    # level (recursively including any embedded call_suspended.child_state), plus every
    # blob-spilled result INLINED (a MemoryStore has no blobs/ directory for the far side to
    # read from later).
    records = engine.store.read_records()
    blobs = {}
    for r in records:
        if r.get("type") == "step_completed" and "result_ref" in r:
            ref = r["result_ref"]
            blobs[ref] = engine.store.read_blob(ref, r.get("result_sha256"))
    derived = engine._state_payload(payload["status"], pending=payload.get("pending"),
                                    result=payload.get("result"), error=payload.get("error"))
    return {"v": SCHEMA_V, "engine": "py", "version": len(records),
            "records": records, "blobs": blobs, "derived": derived}


def _warn_if_oversized(state, flow_id):
    """Warn-only size check for one exported portable-state value. Called ONLY at the top-level
    exporters (run_flow/resume_flow/export_portable_state) — not inside _portable_state, where
    every nested ctx.call suspend would re-serialize its subtree just to measure and the number
    would be an inner subset anyway. Everything still inlines (no external-blob escape hatch
    yet, nested-flows.md "Deferred"); this makes the documented size caveat visible before it
    surprises a DB row/queue-message limit."""
    if not PORTABLE_STATE_WARN_BYTES:
        return
    size = len(_dumps(state).encode("utf-8"))
    if size > PORTABLE_STATE_WARN_BYTES:
        sys.stderr.write("warning: portable state for flow %r is %.1f MB (all blobs inline); "
                         "consider smaller step results — see references/nested-flows.md\n"
                         % (flow_id, size / (1024.0 * 1024.0)))


# ----------------------------------------------------------------------------- flow loading
def load_flow(path):
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    spec = importlib.util.spec_from_file_location("_flow_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001 - a flow that cannot LOAD is a usage error, not a crash
        # bc: parity with engine.js main().catch — message on stderr, EXIT_USAGE (spec-validation
        # errors like workflow._validate_spec surface cleanly instead of a traceback).
        sys.stderr.write("%s: %s\n" % (type(e).__name__, e))
        raise SystemExit(EXIT_USAGE)
    for v in vars(mod).values():
        if getattr(v, "_resumable_flow", False):
            return v
    raise SystemExit(EXIT_USAGE)


# ----------------------------------------------------------------------------- CLI
def _emit(payload, output_file=None):
    line = _dumps(payload) + "\n"
    if output_file:
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(line)
            return
        except OSError as e:
            # bc: a headless driver's designated file is the ONE place it polls for the
            # terminal payload — losing it silently (or crashing the run over a bad path) would
            # be worse than falling back. stdout always still gets the line.
            sys.stderr.write("cannot write --output-file %s: %s -- falling back to stdout\n"
                             % (output_file, e))
    sys.stdout.write(line)


def _state_dir_for(flow_obj, args):
    if args.state_dir:
        return args.state_dir
    home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    return os.path.join(home, "flows", flow_obj.id)


def run_cli(flow_obj, interpreter=None, adjudicator=None, observer=None, argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("run")
    pr.add_argument("--input", default="null")
    pr.add_argument("--state-dir", default=None)
    pr.add_argument("--auto", action="store_true")
    pr.add_argument("--no-strict", action="store_true")
    pr.add_argument("--accept-flow-change", action="store_true")
    pr.add_argument("--output-file", default=None)
    ps = sub.add_parser("resume")
    ps.add_argument("--answer", default=None)
    ps.add_argument("--key", default=None)
    ps.add_argument("--resolve", default=None, choices=["completed", "retry", "abort"])
    ps.add_argument("--resolve-key", default=None)
    ps.add_argument("--resolve-value", default=None)
    ps.add_argument("--state-dir", default=None)
    ps.add_argument("--auto", action="store_true")
    ps.add_argument("--no-strict", action="store_true")
    ps.add_argument("--accept-flow-change", action="store_true")
    ps.add_argument("--output-file", default=None)
    args = parser.parse_args(argv)

    # bc: a designated file for a headless driver to poll instead of capturing this process's
    # stdout — HERMES_OUTPUT_FILE is the env-var equivalent (flag wins if both are given),
    # matching the HERMES_HEADLESS/--auto pairing above. Unset (the common case) -> unchanged:
    # the payload goes to stdout exactly as before.
    output_file = args.output_file or os.environ.get("HERMES_OUTPUT_FILE")

    state_dir = _state_dir_for(flow_obj, args)
    store = FileStore(state_dir)
    try:
        store.acquire()
    except SystemExit as e:
        # bc: every exit carries a machine-readable status line — busy is the retryable one.
        if e.code == EXIT_BUSY:
            _emit({"status": "busy"}, output_file)
        raise
    except OSError as e:
        sys.stderr.write("cannot open state dir %s: %s\n" % (state_dir, e))
        _emit({"status": "error", "error": "state dir not accessible"}, output_file)
        return EXIT_USAGE
    try:
        headless = bool(getattr(args, "auto", False)) or os.environ.get("HERMES_HEADLESS") == "1"
        strict = not getattr(args, "no_strict", False)
        engine = Engine(flow_obj, store, strict=strict, headless=headless,
                        interpreter=interpreter, adjudicator=adjudicator, observer=observer,
                        accept_flow_change=getattr(args, "accept_flow_change", False))
        if args.cmd == "resume":
            # bc: refuse a flow change BEFORE journaling the answer/resolution — a refused
            # resume must leave the gate open (not consume the --answer).
            err = engine.check_flow_change()
            if err:
                _emit(err, output_file)
                return EXIT_SKEW
            resolve_value = (json.loads(args.resolve_value)
                             if args.resolve_value is not None else None)
            usage_err = _prepare_resume(engine, store, raw_answer=args.answer, answer_key=args.key,
                                        resolve=args.resolve, resolve_key=args.resolve_key,
                                        resolve_value=resolve_value)
            if usage_err:
                sys.stderr.write(usage_err + "\n")
                return EXIT_USAGE
            input_value = _input_from_journal(store)
        else:
            try:
                input_value = json.loads(args.input)
            except ValueError as e:
                sys.stderr.write("invalid --input JSON: %s\n" % e)
                _emit({"status": "error", "error": "invalid --input JSON"}, output_file)
                return EXIT_USAGE
        # bc: ONE conversion path for the execute()-originated signals (Corruption/ChildSkew/
        # ResumeReject/headless-blocked) — _execute_shielded, shared with run_flow/resume_flow,
        # so the CLI and library surfaces cannot drift. The clauses below cover only what can
        # fire OUTSIDE execute(): pre-execute journal reads (Corruption) and i/o.
        payload, code = _execute_shielded(engine, store, input_value)
        if engine._resume_ctx is not None:
            # bc: seeded resume token never claimed — the answer was silently dropped (flow
            # shape changed under the resume). Mirrors resume_flow's warning.
            sys.stderr.write("warning: resume answer was not consumed by any open gate "
                             "(flow shape changed?); re-answer\n")
        _emit(payload, output_file)
        return code
    except Corruption as e:
        # bc: pre-execute sites only (check_flow_change/_prepare_resume/_input_from_journal
        # journal reads) — execute()-time Corruption is already converted above.
        sys.stderr.write("journal/blob corruption: %s\n" % e)
        _emit({"status": "error", "error": str(e)}, output_file)
        return EXIT_SKEW
    except OSError as e:
        sys.stderr.write("i/o error: %s\n" % e)
        _emit({"status": "error", "error": str(e)}, output_file)
        return EXIT_USAGE
    finally:
        store.release()


# ----------------------------------------------------------------------------- library API (portable state)
# Alongside the CLI/--state-dir surface above: run_flow/resume_flow take/return the ENTIRE
# resumable state as one self-contained JSON value (see references/nested-flows.md) — no
# --state-dir, no lock, no filesystem at all. A ctx.call child is ALWAYS backed this way
# regardless of what backs its parent; these two functions are the same mechanism exposed as
# the TOP-level entrypoint, for callers who want to own persistence themselves (a DB row, a
# queue message) rather than depend on a directory surviving on a specific machine.
def _execute_shielded(engine, store, input_value):
    """THE single execute()-signal conversion path, shared by BOTH surfaces (run_cli AND
    run_flow/resume_flow) so they cannot drift: Corruption/ChildSkew -> EXIT_SKEW error payloads
    (matching the top-level contract regardless of whether the signal originated at this level
    or bubbled up through nested ctx.call children — see Engine.execute()'s own bare re-raises);
    ResumeReject (a nested resume answer the target level refused) -> a plain EXIT_USAGE error
    payload with nothing consumed (gate still open); a headless flow hitting a gate it cannot
    auto-answer (EXIT_NO_AUTOANSWER) returns an ordinary needs_answer payload instead of letting
    SystemExit kill the caller's process — reusing _derive_status's (already nesting-aware)
    pending-ask computation so this is correct whether the blocked gate is this flow's own
    ctx.ask or buried inside a ctx.call child."""
    try:
        return engine.execute(input_value)
    except Corruption as e:
        # bc: stderr here (not per-surface) so CLI and library behave alike — matching
        # execute()'s own KeyCollision/NonDeterminism stderr writes, which already fire on
        # every surface.
        sys.stderr.write("journal/blob corruption: %s\n" % e)
        return {"status": "error", "error": str(e)}, EXIT_SKEW
    except ChildSkew as e:
        sys.stderr.write("%s\n" % e)
        return {"status": "error", "error": str(e)}, EXIT_SKEW
    except ResumeReject as e:
        return {"status": "error", "error": str(e)}, EXIT_USAGE
    except SystemExit as e:
        if e.code != EXIT_NO_AUTOANSWER:
            raise
        # bc: headless hit a gate it can't auto-answer. The single needs_answer payload is
        # built HERE (not emitted from auto_answer deep inside a possibly-nested child) so the
        # pending key/chain are the hoisted, nesting-aware shape _derive_status computes —
        # matching what exit-10 suspends already report. ask_requested was durably journaled
        # before the raise.
        _status, pending, _result, _error = _derive_status(store.read_records(), store)
        out = {"status": "needs_answer", "pending": pending}
        reason = getattr(e, "reason", None)
        if reason:
            out["error"] = reason
        return out, EXIT_NO_AUTOANSWER


def run_flow(flow_obj, input_value, interpreter=None, adjudicator=None, observer=None, strict=True,
            accept_flow_change=False, headless=False):
    store = MemoryStore()
    engine = Engine(flow_obj, store, strict=strict, headless=headless,
                    interpreter=interpreter, adjudicator=adjudicator, observer=observer,
                    accept_flow_change=accept_flow_change)
    payload, code = _execute_shielded(engine, store, input_value)
    payload["state"] = _portable_state(engine, payload)
    _warn_if_oversized(payload["state"], flow_obj.id)
    return payload, code


def resume_flow(flow_obj, state, answer=None, key=None, resolve=None, resolve_key=None,
                resolve_value=None, interpreter=None, adjudicator=None, observer=None, strict=True,
                accept_flow_change=False, headless=False):
    store = MemoryStore(records=state["records"], blobs=state.get("blobs", {}))
    engine = Engine(flow_obj, store, strict=strict, headless=headless,
                    interpreter=interpreter, adjudicator=adjudicator, observer=observer,
                    accept_flow_change=accept_flow_change)
    err = engine.check_flow_change()
    if err:
        return err, EXIT_SKEW
    try:
        usage_err = _prepare_resume(engine, store, raw_answer=answer, answer_key=key,
                                    resolve=resolve, resolve_key=resolve_key,
                                    resolve_value=resolve_value)
    except SystemExit as e:
        # bc: apply_answer/resolve_in_doubt's OWN validation failures (wrong --key, rejected
        # answer, bad --resolve verb) raise SystemExit(EXIT_USAGE) directly here — the CLI's
        # usual mechanism for a usage exit. A library caller shouldn't have that escape a plain
        # function call, so convert it to an ordinary error payload. Deliberately scoped to ONLY
        # this pre-execute validation step, not _execute_shielded below, which already
        # distinguishes EXIT_NO_AUTOANSWER from a genuine usage error on its own terms.
        return {"status": "error", "error": getattr(e, "reason", "usage error")}, (e.code or EXIT_USAGE)
    if usage_err:
        return {"status": "error", "error": usage_err}, EXIT_USAGE
    input_value = _input_from_journal(store)
    payload, code = _execute_shielded(engine, store, input_value)
    if engine._resume_ctx is not None:
        # bc: the one-shot token was seeded but NO call site claimed it — the flow's shape
        # changed under the resume (the open call resolved/renamed between export and now).
        # The answer was silently dropped; warn so the caller knows to re-answer.
        sys.stderr.write("warning: resume answer was not consumed by any open gate "
                         "(flow shape changed?); re-answer against the returned state\n")
    payload["state"] = _portable_state(engine, payload)
    _warn_if_oversized(payload["state"], flow_obj.id)
    return payload, code


def _derive_status(records, store):
    """Read-only derivation of {status, pending, result, error} from `records` WITHOUT
    re-executing the flow — used by export_portable_state, which must never mutate a live
    on-disk run. Recurses into any open call_suspended exactly like Engine.execute() would if
    it actually resumed (via _hoist_pending), so a hybrid FileStore-backed flow containing
    nested ctx.call children exports the same shape run_flow/resume_flow would produce."""
    for r in records:
        if r.get("type") == "flow_completed":
            return "completed", None, r.get("result"), None
        if r.get("type") == "flow_failed":
            return "failed", None, None, r.get("error")
    for k in {r["key"] for r in records if r.get("type") == "call_suspended"}:
        open_rec = _find_open_call(records, k)
        if open_rec is not None:
            cs = open_rec["child_state"]
            child_store = MemoryStore(records=cs["records"], blobs=cs.get("blobs", {}))
            c_status, c_pending, _c_result, _c_error = _derive_status(cs["records"], child_store)
            if c_status in ("suspended", "in_doubt"):
                pending = _hoist_pending(k, c_pending, in_doubt=(c_status == "in_doubt"))
                return c_status, pending, None, None
    # bc: the LATEST still-open gate (shared _pending_asks fold — the same one apply_answer
    # targets), not the first flow_suspended record (a flow that has answered an earlier gate
    # and since suspended on a LATER one would otherwise be misreported as still waiting on the
    # already-answered one).
    pending_asks = _pending_asks(records)
    if pending_asks:
        p = pending_asks[-1]
        return ("suspended", {"key": p["key"], "question": p.get("question"), "schema": p.get("schema")},
               None, None)
    memo = Memo(store, records)
    if memo.dangling:
        k, attempt = next(iter(memo.dangling.items()))
        return ("in_doubt",
               {"key": k, "attempt": attempt, "interrupted_step": k,
                "options": ["completed", "retry", "abort"]},
               None, None)
    return "running", None, None, None


def export_portable_state(flow_obj, state_dir):
    """Read-only: fold a FileStore run's journal.jsonl + blobs/ into ONE portable JSON value
    (same schema _portable_state() produces), WITHOUT re-running the flow or touching the
    on-disk run in any way. For a caller who wants full CLI/on-disk durability during each
    run/resume call (unlike run_flow/resume_flow's pure-MemoryStore trade-off) but also an
    occasional portable snapshot to move across machines/processes."""
    store = FileStore(state_dir)
    records = store.read_records()
    status, pending, result, error = _derive_status(records, store)
    memo = Memo(store, records)
    engine = Engine(flow_obj, store)
    engine.run_id = memo.run_id
    derived = engine._state_payload(status, pending=pending, result=result, error=error)
    blobs = {}
    for r in records:
        if r.get("type") == "step_completed" and "result_ref" in r:
            ref = r["result_ref"]
            blobs[ref] = store.read_blob(ref, r.get("result_sha256"))
    state = {"v": SCHEMA_V, "engine": "py", "version": len(records),
             "records": records, "blobs": blobs, "derived": derived}
    _warn_if_oversized(state, flow_obj.id)
    return state


def _input_from_journal(store):
    for r in store.read_records():
        if r.get("type") == "run_started":
            return r.get("input")
    return None


def load_hooks(path):
    """Re-import the flow module to pull optional interpreter/adjudicator callables."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_flow_hooks", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return (getattr(mod, "interpreter", None), getattr(mod, "adjudicator", None),
            getattr(mod, "observer", None))


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("cmd", choices=["run", "resume"])
    parser.add_argument("--flow", required=True)
    args, rest = parser.parse_known_args(argv)
    flow_obj = load_flow(args.flow)
    interpreter, adjudicator, observer = load_hooks(args.flow)
    return run_cli(flow_obj, interpreter=interpreter, adjudicator=adjudicator,
                   observer=observer, argv=[args.cmd] + rest)


if __name__ == "__main__":
    sys.exit(main())
