# CLI contract

Both engines expose the same surface and print **one JSON object** on stdout per invocation —
unless `--output-file <path>` (or `HERMES_OUTPUT_FILE`) is set, in which case that ONE JSON
object is written to the file instead and stdout is silent for it (see §Output file below).
Drivers should parse the **last** stdout line — flow code may print above it.

```
python3 scripts/engine.py run    --flow <path> [--input '<json>'] [--state-dir <dir>] [--auto] [--no-strict] [--accept-flow-change] [--output-file <path>]
python3 scripts/engine.py resume --flow <path> --answer '<json-or-text>' [--key <k>] [--state-dir <dir>] [--auto] [--accept-flow-change] [--output-file <path>]
node    scripts/engine.js run|resume --flow <path> ...          # identical
```

`--flow` may be omitted when the flow file is the entrypoint and calls `run_cli(flow)` / `runCli(flow)`
itself (see `examples/`). State lives under `--state-dir`, or `${HERMES_HOME}/flows/<flow-id>/`.

## stdout payloads

| status | shape |
|---|---|
| completed | `{"status":"completed","result":...}` |
| suspended | `{"status":"suspended","pending":{"key":...,"question":...,"schema":...}}` |
| in_doubt | `{"status":"in_doubt","pending":{"key":...,"interrupted_step":...,"options":["completed","retry","abort"]}}` — `options` are exactly the `--resolve` verbs; echo one back |
| failed | `{"status":"failed","error":{"name":...,"message":...}}` |
| needs_answer | `{"status":"needs_answer","pending":{...},"error"?:...}` — exit 12: headless could not (or was not allowed to) answer the gate. Emitted ONCE at the top level; for a gate inside a nested `ctx.call` chain the pending carries the hoisted `key` + `chain` (same shape as exit-10 suspends — `references/nested-flows.md`) |
| busy | `{"status":"busy"}` — exit 13: retry after the holder finishes |
| error | `{"status":"error","error":...}` — exit 2/3 conditions that are detected after startup (skew, corruption, refused flow change) |

## Exit codes

| code | meaning |
|---|---|
| 0 | flow completed |
| 1 | flow failed (terminal error after retries / adjudicator abort) |
| 2 | engine/usage error (bad args, can't load flow, **KeyCollision**) |
| 3 | non-determinism / replay skew, or journal schema too new (refuse to resume) |
| 10 | suspended, awaiting a human/LLM answer |
| 11 | suspended for in-doubt adjudication (a non-idempotent step was interrupted) |
| 12 | headless run could not auto-answer (no `schema.default`, no interpreter) |
| 13 | busy — another process holds the run lock |

## Orchestration recipe

```
run the flow:               engine run --flow F --state-dir D
  exit 0  -> done; read result from stdout
  exit 10 -> read pending.question from stdout; ask the user (or auto-resolve headless)
             engine resume --flow F --state-dir D --answer '<user reply>'
             (may suspend again at the next gate; repeat)
  exit 11 -> a non-idempotent step is in doubt; resolve it (see §In-doubt) then resume
  any other exit -> walk references/driving-failures.md (the failure decision tree,
                    keyed by exit code + stderr; covers 1/2/3/12/13 leaf-by-leaf)
```

## In-doubt resolution (exit 11)

A non-idempotent step that started but never recorded completion (a crash mid-step) escalates with
exit 11 and `pending.key` = the step. After a human/LLM decides whether the side effect actually
landed, resolve it:

```
engine resume --flow F --state-dir D --resolve completed [--resolve-value '<json>']   # it DID land; skip the step (use this value)
engine resume --flow F --state-dir D --resolve retry                                   # it did NOT land; re-run the step once
engine resume --flow F --state-dir D --resolve abort                                   # give up -> flow fails (exit 1)
```

The resolution is journaled (`in_doubt_resolved`) and consulted on replay, so it is deterministic.
`--resolve-key <key>` targets a specific step when more than one is in doubt (otherwise the single
dangling step is inferred). `completed` synthesizes a `step_completed` with `--resolve-value` (default
`null`); `retry` re-executes once (the same idempotency key is forwarded, so a keyed downstream still
dedupes).

A free-form `--answer` (e.g. `'yeah go ahead'`) is routed through the interpreter hook (if provided)
into the schema answer; a JSON `--answer` (e.g. `true`, `'"pro"'`, `42`) is used as-is.

**State is the journal on disk — there is no state blob to pass.** To resume, give `resume` only the
`--answer` and the **same `--state-dir`** used by `run` (or the default `${HERMES_HOME}/flows/<id>/`).
A different/missing state dir starts a fresh run. `resume` takes no `--input` (it is read from the
journal). Driving guide (the orchestrator loop): `references/authoring-and-driving.md`.

## Headless

`--auto` (or `HERMES_HEADLESS=1`) turns a gate into a non-blocking resolution: `schema.default` →
interpreter hook → else exit 12 with a `needs_answer` payload. The chosen answer is **validated
against the gate's schema exactly like `--answer`** — an invalid default/interpreter reply is
rejected (exit 12, nothing journaled, the gate stays open for a corrected answer). A valid answer
is journaled (`interpreted_by: "default"|"llm"`) for audit, so an autonomous run is reproducible.

## Flags

- `--no-strict` disables the replay key-sequence guard (advanced migrations only).
- `flow_hash` mismatch on resume **refuses** (exit 3): editing a step's *body* while keeping its
  key would replay old journaled results against new code — invisible to the key-sequence guard.
  The check runs **before** the answer/resolution is journaled, so a refused resume does not
  consume the `--answer` (the gate stays open). `--accept-flow-change` proceeds and journals a
  `flow_changed {old_hash, new_hash}` audit record; after acceptance the new hash is current
  (the flag is not needed again). The flag propagates into `ctx.call` children (an edited CHILD
  flow is acceptable the same way); accepting while nested calls are still open warns on stderr
  (their embedded child state replays under the new code).
- `--key`/`--resolve-key` targeting a gate inside a nested `ctx.call` chain accepts BOTH the
  hoisted `pending.key` verbatim (`child/gate`) and the bare leaf-local key (`gate`) — precedence
  rules in `references/nested-flows.md`. A wrong key nested is exit 2 **with a payload line**
  (top-level wrong keys exit 2 with stderr only) and consumes nothing — the gate stays open.

## Output file

`--output-file <path>` (or `HERMES_OUTPUT_FILE`, e.g. for a headless driver that would rather
not capture a subprocess's stdout) writes the SAME single JSON payload to that path instead of
stdout, overwriting it each invocation — it is the current terminal status, not a log. The flag
wins if both are set. Diagnostics (stderr warnings, the corruption/i-o-error text) still go to
stderr either way; only the machine-readable payload line moves. If the path itself can't be
written (missing parent directory, permissions), the run is not lost: the payload falls back to
stdout and stderr notes why. Unset (the common case) — behavior is exactly as before this flag
existed.

## Retries and long waits

Per-step retry backoff sleeps **in-process, capped at 60s per wait**. Anything longer contradicts
the suspend-by-exit architecture (a held lock and a waiting process) — model long waits as gates
(`ctx.ask`) until a durable-timer record type exists.
