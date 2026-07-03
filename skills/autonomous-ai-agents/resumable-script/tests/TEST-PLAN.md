# Test plan

Run everything: **`python3 tests/run.py`** — runs the JSON-contract self-checks and the paths
golden matrix, then climbs the escalating ladder, halting at the first failure with a journal
diff. Direct/filtered: `python3 tests/run_ladder.py [-k e3]`.

## Complexity tiers (escalating; simplest first)

The tier suites (`tests/suites.py` `TIERS`) slice the ladder by DEPTH rather than area — disjoint,
climbed in order, each independently runnable:

| tier | covers | rungs |
|---|---|---|
| `tier1-basics` | linear run, memoization, one gate, state channels, sequential fall-through | 6 |
| `tier2-routing` | when-rails/ranges, interpolation across resume, self-declared outcomes, ask-gate routing, judge fallback | 6 |
| `tier3-interrupts` | gate chains, ASK continuation (single/multi-round), scaffold contract, shared threads + gates-as-turns, revision cycles | 8 |
| `tier4-composition` | map fan-out (including suspend-inside-map), search, the workflow-under-`ctx.call` pin, nested library API (2-level/3-level) | 6 |
| `tier5-resilience` | retries, corruption/locks, in-doubt verbs, load rejections, failure routing, in-hash edit-while-parked, flagship investigation | 9 |

Invocation surfaces:
- direct: `python3 tests/run_tiers.py` (climbs all; `--only <suite>` / `--through <tier>`) — writes
  the ground-truth artifact `tests/.last_run.json` `{started, finished, target, tiers, overall,
  exit}` on EVERY path once it runs: a green climb (`overall:"ok"`), a tier failure
  (`"failed"`/exit 1), a bad suite/tier name (`"error"`/exit 2), and a crashing-or-drifted rung
  (`"error"`/exit 1, with the tier's `status:"ERROR"` carrying the reason — distinct from a clean
  `"failed"`). Skipped tiers are recorded `status:"skipped"`. `$RUN_TIERS_ARTIFACT` overrides the
  path (tests point it at a temp file). So a *missing* artifact means only one thing: the process
  never ran at all.
- **via the ask skill**: `./tests/ask_run.sh [tiers|<suite>] [model-alias] [--no-sync]` (the
  `--no-sync` flag is positional-independent) — safe-syncs the dev repo to
  `~/.hermes/skills/resumable-script` (divergence check + timestamped, excluded, retention-bounded
  backup before `rsync --delete`), dispatches an in-container Hermes agent through
  `productivity/ask` (agent mode, `file,terminal` toolsets — the skill's sanctioned QA pattern),
  then takes its VERDICT AND EXIT CODE from the artifact, never from the agent's narrative
  (`ask.py`'s own exit reflects dispatch success only). A missing artifact = "the agent never ran
  the suite" = exit 1. The verdict requires a nonce-matched artifact: a host-minted per-dispatch
  token echoed by the suite proves provenance. This closes accidental/lazy fabrication,
  hallucinated PASS, and stale-artifact reuse; a deliberately adversarial in-container agent is
  out of scope.

The suite is **receipt-based**: it drives the real `run`/`resume` CLI and asserts on exit codes and
journal shape (e.g. `step_started` counts prove a step did / didn't re-run), never on prose.

> **JS mirror quarantined** (YAGNI trim, spec-is-the-product): the Node engine, its `.js` rung
> flows, and its checkers live in `extras/js-mirror/`. The language-neutral contract a second
> engine must satisfy is `references/journal-format.md` + `tests/paths_cases.json` +
> `assets/journal-fixtures/` (rung l09 pins the live engine to those fixtures). Rows below that
> mention JS record history from the dual-engine era.

## Contract checks (run first)
- **contract** (`tests/run.py`) — sorted JSON keys; `NaN`/`Infinity`/`bigint`/`> 2^53-1`
  rejected. Pins the portable format (mirrored by `extras/js-mirror/tests/contract_check.js`).

## Core ladder — mechanism (escalating)
| Rung | Proves | Key receipt |
|---|---|---|
| l00 | linear flow completes | 3 steps started once each, exit 0 |
| l01 | memoization | re-run → `compute` started **once**, identical result |
| l02 | suspend / resume | run exit 10 + `pending`; resume exit 0 |
| l03 | multi-step + suspend | pre-`ask` steps fire once across run+resume |
| l04 | chained suspends | each resume re-suspends at the next gate |
| l05 | conditional branches | only the taken branch's keys appear |
| l06 | loop keying + collision | data-derived keys memoized; dup key → exit 2 |
| l07 | retries + backoff | 3 attempts, 2 `step_failed`, failed never memoized |
| l08 | in-doubt + idempotency | idempotent re-runs; non-idempotent escalates (exit 11) |
| l09 | journal-format pin | normalized py journal matches assets/journal-fixtures/l09-mirror.normalized.json |
| l10 | LLM hooks | interpreter normalizes free-form; adjudicator `skip` |
| l11 | robustness gauntlet | torn-tail drop · blob spill · collision (2) · skew (3) · lock (13) |
| l12 | kitchen-sink | loop+branch+2 suspends+retry compose |

## Property & coverage
| Rung | Proves |
|---|---|
| lprop | **replay determinism** — re-running a completed flow re-executes nothing, identical result |
| lvalues | cross-language **value** portability (unicode, 2^53-1, nesting) |
| lauto | headless `--auto`: `schema.default` → interpreter → exit 12 |
| lidem | idempotency-key **dedupe** happy-path (re-run, downstream applies once) |
| lhelpers | `now()`/`random()`/`uuid()` memoized; `wait()` gate |
| lstate | `state.json` is a correct status pointer at suspend and completion |

## End-to-end scenarios (realistic user stories)
| Rung | Story | Proves |
|---|---|---|
| e1 | **intervention → user fixes the system → resume** | gate suspends; test creates the missing config; resume's dependent step now succeeds |
| e2 | **fail → fix dependency → recover** | a step fails (exit 1); re-run re-attempts ONLY the failed step (`setup` memoized once, `call-api` started twice) |
| e3 | **real crash-window** | side effect lands, process hard-exits (137) before journaling; restart re-runs the step but the idempotency key dedupes → applied **once** (not seeded — a genuine `os._exit`/`process.exit`) |
| e4 | **adjudicator abort** | failed step + adjudicator `abort` → exit 1 |
| e5 | **independent concurrent runs** | two state dirs, two inputs; input survives resume (read from journal); results don't cross |
| e6 | **enforced flow_hash** | a journal from changed source REFUSES to resume (exit 3) even with a matching key sequence; `--accept-flow-change` proceeds + journals `flow_changed`; a refused resume does not consume the `--answer` |

## Guard / robustness
| Rung | Proves |
|---|---|
| g1 | **true concurrency** — two live engines on one dir; the second is rejected (exit 13) while the first holds the lock (overlap-proven, not just an external holder) |
| g2 | re-running a **completed** flow replays to the same result with no new steps; resuming one with no pending gate → exit 2 |
| g3 | **graceful failure** — a non-serializable step result and a glue-code exception both produce a clean `{"status":"failed"}` (exit 1), not a traceback/crash |
| g4 | **structured-object answer** — the user supplies a corrected record (`{"id":7,...}`), not a yes/no, and it round-trips |
| g5 | **`--no-strict`** escape hatch lets a divergent resume proceed (strict would exit 3) |

## Error-condition detection
| Rung | Proves |
|---|---|
| d1 | a malformed line in the **middle** of the journal (not the torn tail) → exit 3 |
| d2 | malformed `--input` JSON → clean usage error (exit 2), not a traceback |
| d3 | a blob whose bytes don't match the recorded `sha256` → exit 3 (no corrupt result returned) |
| d4 | an answer that violates the ask's schema is rejected (exit 2) and **not** journaled, so a corrected answer still works |
| d5 | an unwritable state dir → clean exit 2, not a traceback |

## Regression (reviewer-found bugs — pinned)
| Rung | Pins the fix for |
|---|---|
| r1 | adjudicator **skip** is now memoized (`step_completed` written) — resume does NOT re-run the step or re-invoke the LLM adjudicator |
| r2 | step keys colliding with `Object.prototype` (`"constructor"`, `"toString"`) execute & memoize (JS `Object.create(null)` fix) |
| r3 | a journal written by a **newer schema** (`v` > engine) → exit 3 (was silently read) |
| r4 | `--key` resume rejects a non-open gate (exit 2, no orphan answer); the correct key resumes |
| r5 | **glue purity** — code outside `ctx.step` re-runs every pass; the wrapped step runs once |
| r6 | **positional** strict-replay — a real reorder with a shared prefix diverges at request #1; `--no-strict` allows it |
| inresolve | **in-doubt resolution** — `resume --resolve completed` (synthesize, no re-run) / `retry` (re-run once) / `abort` (exit 1); wrong `--resolve-key` → exit 2. Closes the exit-11 dead end. |

(Also fixed and covered: JS `process.exit` orphaning the lock → thrown `_ExitSignal` so `finally` releases; JS I/O errors → clean payload not a stack trace (d5); JS backoff overflow → capped; `validate_answer` `"null"` type. The false "byte-for-byte" float claim was corrected in `references/journal-format.md`.)

## Rich intervention library — QUARANTINED
The `intervene` library (clarify / propose_change / planful_run) and its rungs (iv1–iv8,
ivnull, ivdup) moved to `extras/intervention/` — it is code-first-only surface the spec
interpreter never imports (the interpreter's interrupt→enrich loop is built in). See
`extras/intervention/README.md` for how to run its ladder.

| Rung | Proves |
|---|---|
| obs | **observer/"thinking" hook** — `before`/`after` on fresh work, `replay` on memo-hits, `ask` on a gate, `failed` on a retry; a throwing observer never fails the flow |

## Known limitations (intentionally not covered)
- **Origin-language is a documented convention, not enforced**: nothing checks a journal's `engine`
  field on resume. (Enforcing it would break legitimate same-flow / different-dir cross-language tests
  and the real cross-resume footgun is exotic.)
- **Mid-file journal corruption**: it is *detected* (exit 3, rung d1) but not *recovered* — only
  tail-truncation re-runs cleanly. CRC framing for bit-rot recovery is deferred (see
  `references/journal-format.md`).
