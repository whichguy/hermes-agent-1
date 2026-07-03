# Authoring flows

A flow is `(ctx, input) -> result` (Python: sync; JS: `async`). `input`, `result`, and every step
result must be JSON-safe. The engine guarantees **deterministic replay + memoization**; that only
holds if you follow these rules.

## The one hard rule: glue must be pure

On resume the engine **re-runs the whole function from the top**. Completed `ctx.step`/`ctx.ask` calls
return journaled values instantly, but *everything between them re-executes*. Therefore:

- **All side effects and all non-determinism go inside `ctx.step`.** A `requests.post`, a DB write, a
  `print`, a counter increment, `time.time()`, `random()`, `uuid4()`, `os.environ[...]`, or a file read
  placed *between* steps fires again on every resume.
- For time/randomness/ids use the memoized helpers: `ctx.now()`, `ctx.random()`, `ctx.uuid()`.
- If you branch, branch on **memoized values** (results of prior steps/asks), never on raw entropy.
- When iterating a dict/map, **sort the keys first** — iteration order is not guaranteed identical
  across runs/languages.

The strict-replay guard records the order of step/ask keys and, on resume, errors loudly (exit 3) at
the first divergence — so a broken-determinism flow fails fast instead of silently mis-memoizing.

## Step keys

- Keys are **explicit strings**, unique within one execution pass. A duplicate key in one pass is a
  fatal `KeyCollision` (exit 2) — it catches the "forgot to vary the key in a loop" bug.
- **Loops**: derive the key from data, not the loop index: `ctx.step(f"charge:{order.id}", ...)`. The
  collection itself must come from a step (so it is identical on replay).
- **Branches**: give each branch distinct keys. Replay takes the same branch (the predicate is a
  memoized value), so only that branch's keys are ever requested.
- **Never rename the key of an already-completed step** — the key *is* the identity of its memo.

## Steps and idempotency

`ctx.step(key, fn, idempotent=True, retries=0, backoff_ms=0)`:

- `fn` is zero-arg, **or** declares a parameter named `idem`/`idem_key` (Python) to receive the
  idempotency key `"<run_id>:<key>"`. Forward that token to the downstream system so *it* dedupes —
  this is what makes a non-idempotent step (charge, email, create) safe across a crash-window re-run.
  - **JS convention (differs from Python):** the engine **always passes the idem key as argument 0**.
    So a JS step fn must be **zero-arg** or take the idem key as its **only** parameter — capture
    everything else in the closure. `ctx.step("k", (idem) => charge(url, idem))` ✓; but
    `ctx.step("k", (url) => charge(url))` would receive the idem key as `url` — wrong. Python is opt-in
    by parameter *name*, so its loop-capture idiom `lambda x=x: ...` is never clobbered.
- A throw is journaled as `step_failed` and **not memoized**, so the step re-attempts on the next run.
- `retries`/`backoff_ms` add in-process re-attempts (exponential backoff) before the throw propagates.
- `idempotent=False` + an in-doubt interruption (a step that started but never recorded completion)
  **escalates** (exit 11) rather than blindly re-running. Pair it with a forwarded idem key.

## Gates: ask / wait / sleep

- `ctx.ask(key, question, schema=None)` — suspends the process (exit 10) until answered; the answer is
  appended to the journal and returned on the next replay. `question` is any JSON object surfaced to the
  human/LLM; `schema` is an optional **advisory** descriptor (`{type, enum?, default?}`) — it is not
  validated, but its `default` drives the headless answer (below).
- `ctx.wait(key, question=None, schema=None)` — the general durable gate; `ask` is the human-facing
  alias. A human is one resolver; a webhook or sibling flow is another.
- `ctx.now()` / `ctx.random()` / `ctx.uuid()` — memoized nondeterminism: each captures its value once
  and replays it verbatim, so time/randomness/ids don't break replay.
- Headless (`--auto` / `HERMES_HEADLESS=1`): a gate resolves via `schema.default`, else the interpreter
  hook, else exits 12 — so an autonomous run is deterministic and intentional, not a coin flip.

## LLM hooks (optional; the engine core stays LLM-free)

Pass callables to `run_cli(flow, interpreter=..., adjudicator=...)` (Python) or export them from the
flow module (`module.exports = { flow, interpreter, adjudicator }` in JS):

- **interpreter** maps a free-form `raw` reply (e.g. "yeah go ahead") into a schema-valid answer; the
  *validated* answer is journaled (`interpreted_by: "llm"`), so replay never re-invokes the LLM.
- **adjudicator** decides `skip` (return a value) or `abort` on a **failed** step; the decision is
  journaled like any answer, so it is deterministic on replay. (An in-doubt non-idempotent step
  escalates via exit 11 for external resolution — it does not go through the adjudicator.)

## Observer hook (the "thinking"/progress narrator — not an LLM hook)

An optional `observer(event)` (module-level fn in Python; `module.exports = {flow, observer}` in JS, or
`run_cli(flow, observer=...)`) is called as steps run, for progress narration / telemetry:
`{phase: "before"|"after"|"replay"|"failed"|"ask", key, attempt?, result?, error?, question?}`.
It is **out-of-band**: not journaled, **runs on every pass including replay** (so it can distinguish
fresh work `before`/`after` from a memo-hit `replay`), and is **try-guarded** — an observer exception
can never fail the flow. Because it re-runs on every replay, keep it cheap and idempotent (emit/log,
not side-effecting); it must not influence control flow. **Treat the event payload as read-only** — it
carries live references to journaled values (e.g. `result`); mutating them would corrupt the memo.

## Appendix: the flow FILE shape (substrate authoring)

This whole page is substrate documentation — the product surface is the workflow spec
(`references/workflow.md`). When you do hand-write a flow file, emit ONE file and invoke it as
`python3 scripts/engine.py run --flow <file.py>`; the CLI puts `scripts/` on the import path and
discovers the flow + hooks automatically.

- Define a **module-level** `@flow(id="...", version=1)` function `(ctx, inp)`.
- *Optional* hooks: module-level functions named **exactly** `interpreter`, `adjudicator`,
  and/or `observer` (discovered by name).
- The `if __name__ == "__main__": run_cli(...)` line is optional (lets the file run itself —
  prepend `scripts/` to `sys.path` first; see `examples/provision_db.py`).

```python
from engine import flow, run_cli

@flow(id="provision-db", version=1)
def provision_db(ctx, inp):
    region = ctx.step("pick-region", lambda: choose_region(inp["hint"]))
    db     = ctx.step("create-db", lambda idem: create_db(region, idem), idempotent=False)
    public = ctx.ask("make-public", {"prompt": f"Expose DB {db['id']}?", "type": "boolean"})
    if public:
        ctx.step("open-fw", lambda idem: open_firewall(db["id"], idem), idempotent=False)
    return {"db_id": db["id"], "public": public}

def interpreter(req):                 # optional: free-form reply -> schema value
    return any(w in (req["raw"] or "").lower() for w in ("yes", "yeah", "sure", "go"))

if __name__ == "__main__":
    run_cli(provision_db)
```

(The JS flow-file shape lives with the quarantined mirror: `extras/js-mirror/README.md`.)
