# Driving failures — the exit-code decision tree

You are the driver: you invoked `engine.py|engine.js run|resume` and got a non-zero exit.
This page tells you what to INSPECT and then exactly what to DO. Evidence is only what the
CLI leaves behind — you never need the engine source:

```
CODE            the exit code — the primary switch
STDOUT          one JSON object (parse the LAST line) — or ABSENT (most exit-2 cases print nothing)
STDERR          the discriminator for exits 2 and 3
D/state.json    {status, pending, result, error, run_id} — fast status; journal is authoritative
D/journal.jsonl grep-able records: step_started / step_failed / ask_requested / flow_failed ...
D/lock          run lock (js: contains holder pid)
```

Golden rules
- Always resume/re-run with the SAME `--state-dir D` and the SAME flow file.
- Re-running is mechanically safe for completed work: completed steps REPLAY, they never
  re-fire — including completed non-idempotent steps. The only risk windows are a dangling
  non-idempotent step (that is exit 11) or a step that threw after a partial external apply
  (verify externally before re-running; see 1.C caveat).
- `step_failed.error.retriable` in the journal means "the engine still had in-process retries
  left", NOT "you should re-run". Use the discrimination in §Exit 1 instead.
- Workflows can pre-empt driver visits **declaratively**: `on_error` (run/search),
  `on_item_error` (map), and `on_exhausted` (prompt/agent) route failures in-flow, so a
  well-authored spec turns many would-be exit-1/exit-10 stops into ordinary walks
  (see `workflow.md` §Failure routing). If you drive the same failure repeatedly, the fix
  may be a spec edit, not a smarter driver.

## Exit 0 — completed
Read `result` from stdout. Done. (Re-running is a harmless full replay to the same result.)

## Exit 10 — suspended (a gate wants an answer)
stdout `pending = {key, question, schema}`.
-> Obtain the answer (surface `question` to the user, or decide it yourself if authorized):
   `engine resume --flow F --state-dir D --answer '<json-or-text>'`
   - JSON (`true`, `'"pro"'`, `42`) is used as-is; free text goes through the flow's
     interpreter hook — if the flow has none, it lands as a raw string, so match `schema`.
   - A specific gate (rare): add `--key <k>`. Default targets the latest open gate.
May exit 10 again at the next gate — loop. Any other exit: re-enter this tree.

## Exit 12 — headless run could not auto-answer
stdout `{"status":"needs_answer","pending":{key, question, schema}}` — the open gate, directly.
-> Pick one:
   a. Answer it yourself: `resume --answer ...` (you may keep `--auto` for the gates after it).
   b. Make the gate autonomous: add `schema.default` to that ask (safe — does not change the
      key sequence) or provide an interpreter hook; then re-invoke with `--auto`.
   c. For a workflow prompt/agent escalate gate: declare `on_exhausted` in the spec so the
      dead-end disappears on future runs.

## Exit 13 — busy (run lock held)
stdout `{"status":"busy"}`. A LIVE process holds D/lock — stale locks never cause 13 (py flock
dies with its holder; js auto-breaks a dead holder's lock).
-> If it is a run you started: wait (poll state.json), retry with backoff.
   If it is your own hung child: kill it, re-invoke — the lock self-clears.
   NEVER delete D/lock while the holder lives. js only: `cat D/lock` = pid; `kill -0 <pid>`.

## Exit 2 — usage (three personalities; discriminate on STDERR)
(Match on the unquoted needle text: py quotes names like `'k'`, js like `"k"` — the surrounding
words are identical in both engines.)
A. stderr `answer rejected for <key>: <why>`   — REJECTED ANSWER (common in a resume loop)
   Nothing was journaled; the gate is STILL OPEN (state.json still "suspended", same pending).
   -> Fix the answer against `pending.schema` (type/enum; mind JSON quoting: a string is
      `'"like-this"'`) and resume again. This is not a flow failure.
B. stderr `KeyCollision: duplicate step/ask key in one pass: <k>` — AUTHORING BUG
   (stdout has `{"status":"error",...}` for this one.) A loop used a static key.
   -> Fix the flow (derive keys from data: `f"item:{id}"`). Then:
      little journaled progress -> simplest is a FRESH state dir;
      expensive completed work  -> keep D, but the fix must leave every already-completed
      key and its order intact, or the next run exits 3.
C. stderr targeting errors — the run is not where you think:
   `resume: no pending ask to answer`      -> read state.json.status: "completed"? you are
                                              done (re-run = exit 0 replay). "failed"? §Exit 1.
   `resume: --key <k> is not an open gate` -> answer state.json pending.key, or drop --key.
   `resolve: no in-doubt step` / `resolve: multiple in-doubt steps [k1,k2]; use --resolve-key`
                                           -> re-check state.json; resolve each listed key
                                              with `--resolve-key`.
   `resolve: --resolve must be completed|retry|abort` -> use exactly one of the three verbs.
D. invocation/load errors: `invalid --input JSON`, `cannot open state dir` (py; js reports the
   same condition as `i/o error: ...`), `resume requires --answer or --resolve`, or a flow-file
   import/spec-validation message.
   -> Nothing ran, nothing was journaled. Fix the command line or the flow file; retry.

## Exit 11 — in-doubt (non-idempotent step interrupted)
stdout `pending = {key: K, attempt, interrupted_step: K, options: ["completed","retry","abort"]}`
(the options ARE the CLI verbs; state.json mirrors this pending object exactly).
The step STARTED but never recorded a terminal — the process died inside it. The engine will
not guess whether the side effect landed. You must find out:
1. Evidence: grep journal.jsonl for the LAST `{"type":"step_started","key":K}` -> its
   `idempotency_key` (`"<run_id>:<K>"`). Query the downstream system by that key, or by the
   step's natural effect (does the row/file/resource exist?).
2. Effect LANDED, and you can state its result value:
   `engine resume --flow F --state-dir D --resolve completed --resolve-value '<json>'`
3. Effect LANDED, no meaningful value:
   `engine resume --flow F --state-dir D --resolve completed`        (records null)
4. Effect did NOT land:
   `engine resume --flow F --state-dir D --resolve retry`            (re-executes ONCE; the
   SAME idempotency key is forwarded, so a keyed downstream still dedupes)
5. CANNOT determine:
   - downstream dedupes on the idempotency key -> `--resolve retry` is safe (exactly-once)
   - dangerous to double-apply and unverifiable -> `--resolve abort` (flow fails, exit 1;
     report K + the idempotency key to the user)
6. Your resolve exits 2 with `multiple in-doubt steps [..]` -> repeat per key via `--resolve-key`.
The resolving invocation immediately continues the flow — re-enter this tree on its exit.

## Exit 3 — skew / corruption (sub-cases; discriminate on STDERR)
A. `NonDeterminism: replay divergence at request #N: journal expected <X>, flow requested <Y>`
   The replayed flow asked for a different key than the journal recorded at position N.
   A1. stderr also shows the flow-change guard (`FlowChanged: flow_hash changed since suspend`,
       or you know you edited the flow): YOUR EDIT broke the journaled prefix.
       -> cheapest: revert the edit, resume.
       -> or keep the edit but re-shape it so every already-journaled key ('X' at #N marks
          the break) is preserved in order; only add/change keys AFTER the journaled prefix
          (then re-invoke with `--accept-flow-change`, which journals a `flow_changed` record).
       -> or start a FRESH state dir — you lose all memoized work. FIRST audit the journal
          for completed non-idempotent steps: their effects exist in the world and a fresh
          run WILL re-fire them. If any are dangerous, carry their results in via --input or
          perform those parts manually.
   A2. no flow edit: the flow is nondeterministic (branches on raw time/random/env, unstable
       iteration order). Authoring bug — route entropy through ctx.step / ctx.now/random/uuid,
       sort collections. The fix inserts keys, so it rarely resumes the old journal ->
       fresh state dir with the same non-idempotent audit as A1.
   `--no-strict` silences this guard; it can hand journaled results to the WRONG keys. It is
   for deliberate migrations only — never a retry button.
B. `journal/blob corruption: journal.jsonl line N is not valid JSON`
   Real damage (a torn final write is dropped silently and never reaches this error).
   -> Preserve D (copy it) for forensics; do NOT hand-repair and resume in place. Fresh state
      dir with the A1 non-idempotent audit; report.
C. `journal.jsonl line N has schema vN, newer than engine v1` — VERSION SKEW
   -> Touch nothing. Resume with the newer engine that wrote it. If unavailable, park the
      run — it stays resumable. Do not --no-strict around this (it does not help anyway).
D. `blob <ref> failed its sha256 integrity check`
   A spilled step result no longer matches the sha the journal recorded.
   -> Restore the exact blob bytes (backup/VCS) and re-run; otherwise that memo is
      unrecoverable: preserve D, fresh state dir (A1 audit), report.
E. `FlowChanged: flow_hash changed since suspend` (alone, no divergence yet)
   The engine refuses to resume under an edited flow without an explicit ack.
   -> If the edit preserves the journaled key prefix: re-invoke with `--accept-flow-change`.
      If unsure, diff your edit against the journaled keys first (A1 rules apply).

## Exit 1 — flow failed (diagnose LOCALITY first, then cheapest action)
stdout `error = {name, message[, step, attempts]}`; journal tail has `flow_failed` and any
`step_failed` records. (`step`/`attempts` are present when the failure came from a step —
they identify the failing key and the CUMULATIVE attempt count across invocations.)
A. error.name == "aborted" (`adjudicator aborted at <key>` / `in-doubt step <key> aborted by
   resolution`) -> DELIBERATE HALT, terminal by policy. Report; do not re-run.
B. STEP failure or GLUE failure?
   Evidence: `error.step` present -> step failure at that key. Absent -> GLUE/RESULT failure
   (code between steps or the return value: `... is not JSON-safe`, `integer ... exceeds
   2^53-1`, TypeError/KeyError from the flow body).
   GLUE -> LOCAL-METHOD: fix the flow's glue. Glue re-runs on every pass, so a glue-only fix
   does not disturb the key sequence -> re-run the SAME state dir; completed steps replay.
   (If the fix adds/renames/reorders ctx.step/ctx.ask keys, read §Exit 3.A1 first.)
C. STEP failure at key K — transient or deterministic? Gather from journal.jsonl:
     N   = count of step_failed records for K   (== error.attempts; accumulates across runs)
     E_i = each attempt's error {name, message}
     R   = count of run_started records (how many invocations have been tried)
   C1. N == 1 and E looks environmental (timeout, connection refused, 429/5xx, missing dep,
       held lock) -> LOCAL-TRANSIENT: re-run the same command, same D. SAFE — the failed step
       was never memoized; everything completed replays. Cheapest action first.
   C2. E identical (same name AND message) across >= 2 attempts spanning >= 2 invocations
       (R >= 2) -> DETERMINISTIC: re-running is pointless. LOCAL-METHOD:
       a. environmental cause (config/dep/service) -> fix out of band, re-run same D
          (the fix-then-rerun loop; only the failed step re-executes);
       b. the step body is wrong -> edit it. Editing a failed step's BODY is safe: its key is
          unchanged and it holds no memo. Re-run same D;
       c. unfixable -> GENUINE EXHAUSTION: abandon + report (see §Stopping).
   C3. E varies across attempts -> flaky: re-run within a small budget (2 more), then
       BUDGET-HALT — stop and surface K, N, and the error history to a human.
   Caveat: if the flow marks K non-idempotent (a workflow `"idempotent": false` state, or
   `idempotent=False` in a hand-written flow — read the FLOW file, not the engine) and the
   error suggests a partial apply, verify the external system as in §Exit 11 before any re-run.
   Recurring visits here? Consider declaring `on_error` rules on the failing state
   (`workflow.md` §Failure routing) so the spec handles this class next time.

## Stopping (terminal taxonomy)
- SUCCESS: exit 0 — report result.
- GENUINE EXHAUSTION: deterministic unfixable failure (1.C2c), unrecoverable corruption
  (3.B/3.D), deliberate aborts (1.A). Report flow_id, run_id, failing key, attempts, error
  history, and the state-dir path — PRESERVE D; the journal is the audit trail.
- BUDGET-HALT: flaky beyond budget (1.C3), an unwaitable lock (13), an unobtainable answer
  (10/12). PARK the run: nothing is lost — the same state dir resumes later from exactly here.

## Future direction
The natural endpoint of this tree is an executable driver (`scripts/drive.py`-style, as in
the method-explorer skill): backoff-retry on 13, answer-or-park on 10/12, the §Exit-1
discrimination run mechanically, resolve prompts on 11 — surfacing only genuinely human
decisions. It should be built from THIS document once the tree has stabilized in real use;
until then, this page is the canonical walk.
