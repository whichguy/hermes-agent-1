# Nested flows — `ctx.call` and portable state

Everything else in this skill composes suspend/resume by sharing ONE journal: `map`'s per-item
`decide` gates (`wf_mapsusp`) and the `prompt`/`agent` interrupt→intervene loop
(`wf_intervene_multi`) both work by namespacing keys inside a single flow function — a hierarchical
key like `scan#0/map#3/gate` durably records the call stack for free, as an ordinary string, with
zero new engine machinery. If that's what you need — a reusable *helper* that shares its caller's
journal — write it as a plain function taking `ctx` and call it directly; no special primitive is
required.

`ctx.call` is for something stronger: an **independent, reusable child flow** — its own `Flow`
object, its own durable state, callable from more than one place — where a suspend anywhere in the
chain must bubble up to the true top automatically, to arbitrary depth, and where **the entire
resumable state is one self-contained, portable JSON value** you can store, move across processes,
or hand to a completely different machine.

## The core idea: no separate stack is ever maintained

A parent's `ctx.call("child", child_flow, input)` runs the child through its own nested `Engine`,
always backed by an in-memory `MemoryStore` (never a `--state-dir`). If the child suspends or goes
in-doubt, the parent's `ctx.call` doesn't see an ordinary result — it sees `ChildSuspend`/
`ChildInDoubt`, which `Engine.execute()` converts into the **parent's own** suspended/in-doubt
payload, with the child's key prefixed on (`"child/gate"`) and the child's *entire* state embedded
as one journal record: `{"type": "call_suspended", "key": "child", "child_state": {...}}`.

That's the whole trick. A grandchild's suspend becomes the child's `ChildSuspend`, which becomes
the parent's `ChildSuspend`, via ordinary Python exception propagation through nested `ctx.call`→
`Engine.execute()` calls — nobody writes code that "walks the tree." And because the child's full
state is *embedded*, not referenced, nesting a state blob under another state blob **is** nesting a
journal record under another journal record — the same mechanism `journal-format.md` already uses
everywhere else, one level more general. There is no separately-serialized "call stack" anywhere;
the stack is either the live Python call stack during one invocation, or (on disk / in the
returned blob) just an ordinary, recursively-nested journal.

## Portable state

```jsonc
// what run_flow/resume_flow return whenever the flow is suspended/in_doubt
{
  "status": "suspended",
  "pending": {
    "key": "child/leaf/gate",             // "/"-joined path, root -> leaf
    "question": {...}, "schema": {...},   // HOISTED from the deepest open ask, for display
    "chain": ["child", "leaf", "gate"]    // breadcrumb of local (unqualified) keys, root -> leaf
  },
  "state": { /* hand this back verbatim on resume, from ANY process */ }
}
```

```jsonc
// the "state" object — identical recursive shape at every depth
{
  "v": 1, "engine": "py", "version": 7,   // "version" = an opaque, monotonic optimistic-concurrency token
  "records": [ /* exactly journal.jsonl's records, in order */
    ..., {"type": "call_suspended", "key": "child", "child_state": { /* THIS SAME SHAPE, recursively */ }}
  ],
  "blobs": { "<ref>": <inlined value> },  // every blob-spilled result, resolved and inlined
  "derived": { /* the same fields state.json carries: flow_id, run_id, status, pending, result, error, ... */ }
}
```

No separate `children` map exists anywhere — a suspended `ctx.call` embeds the child's entire
portable-state object directly inside its one `call_suspended` record, so the parent's `records`
array is already, automatically, everything.

## The API

```python
from engine import run_flow, resume_flow, export_portable_state

payload, code = run_flow(my_flow, input_value)
# code == 10 (suspended): payload["state"] is the blob above; store it however you like — a DB
# row, a queue message, a browser session. Show payload["pending"]["question"] to the user.

payload2, code2 = resume_flow(my_flow, state=payload["state"], answer='"approve"')
# same shape back: completed, or suspended again (possibly at a DIFFERENT depth than before —
# a chain can get shallower as inner gates resolve, not just deeper).
```

`run_flow`/`resume_flow` are **library functions only** — no CLI flags, no `--state-dir`, no lock.
They mirror `run_cli`'s internal shape (build an `Engine`, apply the answer/resolve if resuming,
call `execute()`, emit the payload) but swap `FileStore` for `MemoryStore` at every level,
including every nested `ctx.call` child regardless of what backs the top level. Both accept
`interpreter=`/`adjudicator=`/`observer=`/`strict=`/`accept_flow_change=`/`headless=`, mirroring
the CLI's own hooks and flags one for one — including across a `ctx.call` boundary: a failed
child consults the PARENT's adjudicator exactly like an ordinary failed `ctx.step` would (`skip`
memoizes a value and lets the parent continue, `abort` fails with `name="aborted"`), and a
`headless=True` flow that hits a gate it cannot auto-answer (`schema` has no `default`, no
`interpreter` injected) — however deep inside a `ctx.call` chain — returns an ordinary
`({"status": "needs_answer", "pending": {...}}, 12)` tuple instead of raising `SystemExit`
through your code; nothing the flow already completed is lost, and a later `resume_flow` call
(headless or not) picks up exactly where it left off.

**Caveat, not a contract:** neither function shields a step body that calls `sys.exit()` for its
own unrelated reasons (a stray dependency doing this, say) — that's a pre-existing, engine-wide
characteristic (the CLI has the exact same exposure) unrelated to `ctx.call`, not something either
function tries to catch.

`export_portable_state(flow_obj, state_dir)` is the hybrid escape hatch: point it at a real,
on-disk `--state-dir` run and get back the same portable blob, **read-only** — the on-disk run is
untouched and keeps resuming normally via the CLI afterward. Use this if you want full durability
during each `run`/`resume` call (see the trade-off below) but still want an occasional portable
snapshot to move the run elsewhere.

## Addressing a nested gate by key (`key=` / `--key`, `resolve_key=` / `--resolve-key`)

You normally pass no key at all — the latest open gate is the target, at whatever depth. When you
DO pass one, the precedence rule, applied identically at every level:

1. **Local exact match wins** — a key exactly matching a currently-open gate (or dangling step,
   for resolve) at the current level applies there, even if an open `ctx.call` also exists.
   `"/"` is legal inside plain step keys (`map` builds `scan#0/map#3`-style keys), so an exact
   local match must always beat path interpretation.
2. **Exact-prefix strip** — otherwise, a key starting with `<open-call-key> + "/"` sheds that one
   prefix and routes into that call; repeat per level. This is why **the `pending.key` the API
   hands you round-trips verbatim**: `resume_flow(..., key="child/leaf/gate")` (or CLI
   `--key child/leaf/gate`) lands at the right depth.
3. **Pass-through** — otherwise the key is handed down unchanged, so the bare leaf-local form
   (`key="gate"`) also works at any depth.

A key that matches nothing is **rejected without consuming anything**: no record is journaled,
the gate stays open, and the same state resumes fine with a corrected key. Via the library this
is an ordinary `({"status": "error", "error": "..."}, 2)` return (never an escaping
`SystemExit`); via the CLI it's exit 2 with the same payload line. Two more nothing-consumed
guarantees ride the same mechanism: a nested **flow-change refusal or replay divergence**
surfaces as `({"status": "error", ...}, 3)` at the top — not a permanent `flow_failed` — and if
a seeded resume answer is never claimed by any open call site (the flow's shape changed under
the resume), a stderr warning tells you the answer was dropped rather than mis-applied.

## The trade-off you're accepting (read this before using `ctx.call`)

The on-disk `FileStore` model gets crash safety for free: a single-writer lock plus one
fsync'd append per record means a process dying mid-step leaves a durable, inspectable "dangling
start" — the engine escalates to in-doubt (exit 11) rather than guessing whether a non-idempotent
side effect landed. A `MemoryStore`-backed run has **no** incremental durability during a pass —
if the process dies before `run_flow`/`resume_flow` returns, there is nothing anywhere to detect
that, because nothing was ever written to durable storage. This is inherent to choosing the
portable-blob model, and **a `ctx.call` child always pays this cost**, even under an otherwise
fully-durable `FileStore` parent, because a child is unconditionally `MemoryStore`-backed.

**The rule:** don't put a non-idempotent, in-doubt-sensitive side effect (a charge, a deploy)
inside a `ctx.call` child. Keep those as a top-level `ctx.step(..., idempotent=False)` under a
real `--state-dir`. Reserve `ctx.call` for children whose own steps are idempotent, or for cases
where portability is the actual goal and this cost is accepted.

A second, related risk: nothing stops two callers from independently resuming the *same* stale
blob — there's no lock-equivalent for a `MemoryStore`. Each `state["version"]` (an opaque,
monotonic record count) exists so **your own storage** can do optimistic concurrency — only accept
a written-back blob if the row's version still matches what you read. The engine has no visibility
into wherever you persist the blob, so it can't enforce this for you.

A smaller limitation: an `agent` kind step needs a real `--state-dir` for its live MCP
`state_mcp.py` server (`Context.state_dir` is `None` for a `MemoryStore`-backed engine) — the
`agent` kind cannot run inside a `ctx.call` child, or as the top level of `run_flow`/`resume_flow`.
The interpreter enforces this with a clear error at the `agent` dispatch (an ordinary,
well-labeled step failure), rather than letting it die as a `TypeError` inside the agent caller.

**`accept_flow_change` + a still-open `ctx.call` is a bigger blast radius than an ordinary key.**
`--accept-flow-change`/`accept_flow_change=True` is already an explicit "I know what I'm doing"
escape hatch for any key rename/kind swap; it works exactly the same way for a `ctx.call` key —
but if THAT key had a still-open, embedded `call_suspended` (an entire nested sub-journal,
potentially with its own open human gate several levels down), accepting the change discards the
*whole* embedded child state, not just one step's memo. The engine now warns loudly on stderr
(naming the open call keys) when an accepted flow change has open nested calls — but it never
refuses (the flag is explicit intent). The flag also propagates INTO child engines, so an edit to
a CHILD flow's source under an open `call_suspended` is likewise acceptable with the same flag
(and refused, resumable, without it). Don't rename/repurpose a `ctx.call` key while it has an
unresolved `call_suspended` unless discarding everything under it is actually what you want.

**`pending`'s shape depends on whether a `ctx.call` boundary was crossed — don't assume `chain`
is always present.** A plain (non-nested) suspend/in-doubt has exactly the shape the CLI has
always returned (`{"key", "question", "schema"}` or `{"key", "attempt", "interrupted_step",
"options"}`) — no `chain` field at all. `chain` (and the `"/"`-joined `key`) only appears once at
least one `ctx.call` boundary was hoisted through. Read `pending.get("chain")`, don't index it.

## Failure policy at the call boundary: `on_fail="catch"` (and why there is no retry)

`ctx.call(key, child_flow, input, on_fail=...)` mirrors `ctx.step`'s catch semantics for a
whole-child failure: `on_fail(error_dict, attempt)` returning `{"action": "catch"}` memoizes the
step-style sentinel `{"__error__": {"name", "message", "attempts"}}` as the call's **permanent**
result (replay deterministically re-takes the failure branch — branch on `"__error__" in result`
in the parent). `on_fail` supersedes the adjudicator, exactly like on a step; without it, the
failed child consults the parent's adjudicator (`skip`/`abort`). There is deliberately **no
`retries=` on a call**: a failed child persists nothing, so a retry would re-run the entire child
from scratch, re-firing every side effect with zero partial credit — the exact hazard the
in-doubt machinery exists to prevent. Re-invoke the parent after fixing the cause instead (the
memoized prefix replays for free).

## Observer events at call boundaries

Additive to the existing `before`/`after`/`failed`/`replay` vocabulary (all out-of-band,
unjournaled):

- `{"phase": "call", "key", "flow_id", "resumed", "desc"}` — a call boundary is being crossed
  live (`resumed` distinguishes a fresh child from one reconstituted mid-suspend). Not emitted
  on replay — a memoized call emits only the ordinary `{"phase": "replay"}`.
- `{"phase": "call_suspended", "key", "pending", "in_doubt", "desc"}` — the child's
  suspend/in-doubt is propagating up through this site (`pending` is the CHILD-local shape;
  `"blocked": True` is added on the headless can't-auto-answer path).
- A completed call emits the ordinary synthesized `{"phase": "after", "synthesized": True}` via
  its memoization; the child's own internal events flow through the SAME observer callable with
  their child-local keys.

## Deferred

- ~~A workflow-spec kind for invoking a child flow declaratively~~ — **LANDED as the `flow` kind**
  (`references/workflow.md` §3c): references resolve in a `load_workflow(..., flows={name: spec |
  Flow})` registry (inline spec = a dict value), validated eagerly / compiled lazily, executed via
  `ctx.call` with hoisted verbatim-key resume, the child's `{result, state}` auto-stored at
  `$.<state>`. The name-collision question is resolved: `flow` = child workflow; `tool` remains
  reserved for the future generic injected-TOOL kind (workflow.md §Injected-tool kinds). Pinned by
  rungs `call_wf_child` (workflow-spec child under a code-first parent) and `wf_flow` (spec parent,
  loops = fresh children).
- An external-blob escape hatch for oversized portable states — today every blob-spilled result is
  always inlined into the exported blob, so a flow with very large step results produces a large
  export. The top-level exporters now WARN on stderr past `HERMES_FLOW_PORTABLE_WARN` bytes
  (default 8 MB, 0 disables) so this stops being invisible; the escape hatch itself stays
  deferred until a real caller needs it.

**In-hash memo validity nests for free**: a child's model-call `in_hash`es live inside its own
records, which are what `call_suspended.child_state` embeds — so editing a child spec mid-park
selectively re-executes inside the child on resume, same rule at every depth.

## Tests

Everything above is pinned by the `"nested-call"` suite (`tests/suites.py`), which spans BOTH
ladders — run it with `python3 tests/run_ladder.py --suite nested-call`:

- CLI/`FileStore`-backed `call_*` rungs (`tests/run_ladder.py`): `call_cli_2level`,
  `call_cli_3level`, `call_crashboundary`, `call_collision`, `call_memo_strict_gap`,
  `call_key_target` (path-aware `--key` addressing incl. nothing-consumed rejection),
  `call_auto_nested` (CLI `--auto`: one hoisted needs_answer emit + durable child state),
  `call_statejson` (state.json mirrors the hoisted stdout payload).
- Library-API `rf_*` rungs (`tests/run_call_ladder.py`): `rf_2level`, `rf_3level`,
  `rf_failed_child`, `rf_in_doubt_nested`, `rf_crash_toplevel`, `call_export_hybrid`,
  `rf_headless_nested`, `rf_child_adjudicator`, `rf_child_corruption`,
  `rf_derive_status_latest`, `rf_resolve_key_nested` (path-aware `resolve_key`),
  `rf_child_retry_catch` (step retries + step/call `on_fail` catch inside a child),
  `rf_child_blob` (genuine blob spill across the boundary, both directions),
  `rf_sibling_calls` (one-shot token safety with two sibling calls + flow-object reuse),
  `rf_child_runid_wait` (child run_id stability + child-rooted idempotency + `ctx.wait`),
  `rf_child_interpreter` (interpreter auto-answer on nested gates + the top-level
  bare-`ask_answered` key_order regression), `rf_derive_drift` (`_derive_status` vs live
  execution lockstep), `rf_deep_chain` (12 levels: full chain, linear embedding, deep explicit
  key), `rf_call_observer` (the observer vocabulary above), `rf_nostrict_nested`.

The narrated demo `examples/walkthrough_nested.py` (self-checking, offline) walks the whole
story — hoisted suspend → one-JSON-value state → verbatim-key resume with an exactly-once proof
→ the fork risk — and runs as part of `python3 tests/run.py` alongside both ladders.
