# Journal format — the language-neutral contract

This is the **normative** on-disk contract, and it is deliberately language-neutral: any engine
implementation must conform to it. The live engine is `scripts/engine.py`; a JS mirror conforming
to the PRE-`call_suspended` version of this contract (schema v1 without nested flows) is
quarantined in `extras/js-mirror/` — it predates `ctx.call`, so resurrecting it now also means
implementing `call_suspended` + the portable-state embedding (see §Portability for the
resurrection contract). Integer/string/boolean/null values serialize **byte-identically** across conforming
engines; **floating-point** values are equal-when-parsed but may differ in text (Python `5.0` vs
JS `5`; `1e-07` vs `1e-7`), and `ctx.now()` resolution may differ per runtime — so avoid floats in
journals that must be cross-audited, or carry them as strings. A given *run* resumes only in the
engine it started in (see §Portability).

## Layout

```
<state-dir>/                      # --state-dir, or ${HERMES_HOME}/flows/<flow-id>/
  journal.jsonl                   # append-only event log — THE source of truth
  state.json                      # derived status pointer (atomic temp+rename); rebuildable
  blobs/<key>.<attempt>.json      # sidecar for step results over HERMES_FLOW_BLOB_THRESHOLD (64 KB)
  lock                            # exclusive run lock (flock); a second writer exits 13
```

## JSON encoding (pinned so values round-trip across Python and JS)

- `allow_nan=False` — NaN/Infinity are rejected at write time.
- `ensure_ascii=False` — UTF-8, no `\uXXXX` escaping.
- **Keys sorted** lexicographically (`sort_keys=True` / stable stringify).
- Integers must be within ±(2^53−1), else carry them as strings.
- Never rely on a trailing `.0` to distinguish int from float: a whole-number float renders
  divergently — Python `1.0` vs JS `1` (e.g. inside a `${...}` hole). Carry such values as ints
  or strings if cross-language byte-parity matters.
- Timestamps are ISO-8601 UTC `...Z`. Bytes are base64 strings.
- Python `None` and JS `undefined` both serialize to `null` (never dropped) — so an absent optional
  field round-trips identically across engines.
- Step results, inputs, and answers must be JSON-safe; non-serializable values (NaN/Infinity/bigint/
  out-of-range int) are rejected at journaling time with the offending key (no silent coercion).

## journal.jsonl — one JSON object per `\n`-terminated line

Every line carries `v` (schema version, currently `1`), `seq` (0-based ordinal),
`ts` (ISO-8601 `...Z`), and `type`.

| `type` | additional fields | meaning |
|---|---|---|
| `run_started` | `run_id`, `flow_id`, `flow_version`, `flow_hash`, `engine`, `engine_version`, `input` | one per process invocation (run or resume). `run_id` is the **stable logical run id**, created once and reused on every resume. |
| `step_started` | `key`, `attempt`, `idempotency_key` | about to execute the step body. `idempotency_key = "<run_id>:<key>"`. |
| `step_completed` | `key`, `attempt`, `result` **or** (`result_ref`, `result_sha256`), optional `in_hash` | success → **the memo record**. `in_hash` (`"sha256:<hex>"`) records a hash of the step's declared INPUT (the workflow layer hashes each model call's exact rendered conversation and each search's rendered query): on replay the memo is honored ONLY if the demanded hash matches; records WITHOUT `in_hash` are unconditionally valid (legacy). Per key, the NEWEST valid record wins. A declaratively CAUGHT failure (adjudicator skip, in-doubt `completed`, workflow `on_error`) memoizes a *synthesized* `step_completed` — same record type, no schema change; a caught `on_error` result is the error sentinel `{"__error__":{name,message,attempts}}` and follows that attempt's `step_failed`. |
| `step_failed` | `key`, `attempt`, `error{name,message,retriable}` | informational; never a memo hit. |
| `ask_requested` | `key`, `question`, `schema` | a gate (`ask`/`wait`) was reached. |
| `ask_answered` | `key`, `raw`, `answer`, `interpreted_by` (`human`/`llm`/`default`) | answer → memo record. |
| `in_doubt_resolved` | `key`, `action` (`completed`/`retry`/`abort`), `value`? | an orchestrator's resolution of an in-doubt step (see `cli-contract.md` §In-doubt). |
| `flow_changed` | `old_hash`, `new_hash` | a human accepted a flow-source change under a live journal (`--accept-flow-change`); audit record. |
| `flow_suspended` | `pending_key` | terminal for this process; awaiting an answer. |
| `flow_completed` | `result` | terminal success. |
| `flow_failed` | `error{name,message,step?,attempts?}` | terminal failure. `step`/`attempts` (failure provenance: which step, after how many tries) are present only when the failure came from a step — glue errors keep the bare shape. |
| `memo_invalidated` | `key`, `old_hash`, `new_hash` | the walk demanded `key` with a different `in_hash` than the recorded one (the definition or a rendered input changed): everything journaled from this key's first occurrence onward is stale HISTORY — folding truncates `key_order` at that point (the re-executed walk may legitimately diverge), the stale memo is skipped, and the step re-executes (a fresh started/completed pair for the SAME key follows). Keyed maps survive: `ask_answered` records (human answers) always replay; a later re-demand with a matching hash replays the newest valid record. |
| `call_suspended` | `key`, `child_state` | a `ctx.call` child came back suspended/in_doubt. `child_state` embeds the CHILD's own entire portable-state object (§`nested-flows.md`) recursively — a whole sub-journal nested one level down, not a reference. On completion the call memoizes an ordinary `step_completed` instead; `call_suspended` never appears for a call that resolved cleanly. |

### Derived view (memoization)
Scan the journal once:
- `completed[key] = result` from `step_completed` (resolving `result_ref` via `blobs/`).
- `answered[key] = answer` from `ask_answered`.
- `dangling[key] = attempt` for a `step_started` with no matching terminal = **in-doubt**
  (the process died mid-step).
- `key_order` = first-request order of every step/ask/**call** key, used by the strict-replay
  guard — an in-flight, still-open `call_suspended` joins `key_order` immediately (exactly like
  `ask_requested`), so renaming an unresolved `ctx.call`'s key raises `NonDeterminism` rather than
  silently passing. An `ask_answered` also notes its key — a no-op when the gate's `ask_requested`
  already did, but load-bearing for the headless default/interpreter path, which journals a BARE
  `ask_answered` (no `ask_requested`) at exactly the position the gate was reached; synthetic
  `__adjudicate:` audit records are excluded (no flow ever requests those keys).

### Crash safety
- Each record is one UTF-8 buffer written with a single `os.write` to an `O_APPEND` fd,
  then `fsync`; the parent directory is `fsync`ed on journal creation.
- An exclusive `flock` guarantees a single writer (so `O_APPEND`'s platform size cap —
  ~256 B on macOS — never causes interleaving).
- On read, a trailing line **not** terminated by `\n` is dropped: a torn final write
  never durably happened, so the step it would have recorded simply re-runs.

## state.json — derived status pointer

`{v, flow_id, flow_version, flow_hash, run_id, status, pending, result, error, engine, engine_version}`
where `status ∈ {running, suspended, in_doubt, completed, failed}`. It is a cache for fast
status reads; if missing or corrupt, rebuild it from `journal.jsonl` (the journal is authoritative).

## Portability

The **format** is shared; a **run** does not hop engines. `flow_hash` is a hash of flow
*source* — and serialized values can differ subtly across runtimes — therefore a run resumes
in its origin engine. The shared guarantee for any conforming pair of engines is **mutual
parseability and identical semantics** (parse, validate, audit each other's journals) — not
byte-identity: floats, timestamp resolution, and `flow_hash` are the documented carve-outs.

**Resurrection contract.** The JS mirror was quarantined to `extras/js-mirror/` (the only
production host is Python). A second engine — that mirror revived, or a fresh one — is
conformant when it (a) implements this record schema and the JSON encoding rules above,
(b) passes the `${...}`/JSONPath golden matrix `tests/paths_cases.json`, and (c) reproduces
the normalized journal fixtures in `assets/journal-fixtures/` (rung `l09` pins the live py
engine to the same fixtures).

The single-writer **lock** enforces the origin-engine rule at the seam: conforming engines
write their PID into `lock`; a holder that is provably dead is stale (taken over), while an
unreadable/live holder fails closed (exit 13). If an invocation reports busy on a dir whose
holder crashed, remove `lock` by hand.

## Versioning

An engine reading a journal with a higher `v` than it understands refuses (exit 3); unknown
*fields* within a known `v` are ignored (forward-compatible). `flow_hash` mismatch on resume
**refuses** (exit 3) — a step *body* edited under a live journal is invisible to the
key-sequence guard, so it must be accepted explicitly (`--accept-flow-change`, journaling
`flow_changed`). The key-sequence + strict-replay guard remains the structural invariant and
errors loudly (exit 3) at the first divergence in already-journaled territory.
