# Agentic investigation flows

How to model a **durable, resumable codebase investigation** on this engine — the kind of long
"triage a failing test / track down a regression" session an agent runs, where the expensive
exploration must survive interruptions, failures, and human hand-offs. Worked example:
`examples/investigate_repo.{py,js}`, driven by `examples/walkthrough_investigate.py`.

## Why the engine fits

An investigation is exactly what durable execution is for: a sequence of expensive, side-effecting,
sometimes-failing steps punctuated by human decisions, that you must be able to **pause and resume
without redoing** — across a crash, a context limit, or an engineer stepping away. Map the moments:

| Investigation moment | Engine mechanism |
|---|---|
| Scan / index the repo (expensive) | `ctx.step` **memoized** — never re-scanned on resume |
| Run a command that hits a broken environment | step **fails cleanly**; fix out of band; resume re-runs only it |
| A flaky / transient failure | bounded **retry** loop (distinct step keys) → escalate to a human gate |
| An ambiguous finding (which file? real or noise?) | `ctx.ask` **decision gate**, typed to the candidate set |
| Approval before a destructive/mutating action | `ctx.ask` **approval gate** |
| A mutating edit that crashes mid-write | **non-idempotent** step → **in-doubt** (exit 11), never a blind re-apply |
| Confirm the fix | a final verify step; the whole run **replays for free** afterward |

## The core pattern: one flow, two tool backends

Write the flow **once** against a thin tool layer (`scan` / `run_tests` / `grep` / `read` / `apply_fix`
/ `propose_fix`), and make the backend swappable. This is what lets the same flow power both a fast,
deterministic test tier and a real run:

- **fixture backend** (`INVESTIGATE_MODE=fixture`) — canned, **sorted, stable** outputs from a JSON file
  (`$INVESTIGATE_FIXTURE`). Fully deterministic → lives in the hermetic ladder (`inv_*` rungs).
- **real backend** (`INVESTIGATE_MODE=real`) — actual glob / `subprocess` / file-read/-edit under
  `$INVESTIGATE_ROOT`. Proves the flow works on real source (`tests/run_integration.py`).

Because both backends satisfy the same tool contract, the hermetic rungs exercise the *same* flow that
runs for real — no test-only divergence. See `examples/investigate_tools.{py,js}`.

### Rules that keep it replay-safe

- **All I/O goes inside `ctx.step`.** The flow body (classify, symbol extraction, path sorting) must be
  pure — it re-runs on every resume.
- **Deterministic tool output.** Sort glob/grep results; canonical JSON; don't leak timestamps, absolute
  temp paths, or the idempotency key into a step's return value (it would break replay / cross-engine
  parity). `apply_fix` returns `{patched: path}`, not the `idem`.
- **Memoize the analysis.** Wrap the (expensive, in reality LLM-driven) fix proposal in a `propose` step
  so it is never recomputed on resume. In fixture mode it reads a JSON `edit`; in real mode a repo
  `proposed_fix.json` — co-designed with the planted bug so the real edit genuinely fixes it.
- **Mark mutations non-idempotent.** An edit/patch/migration isn't safe to blindly repeat; declaring the
  step `idempotent=False` makes a mid-write crash escalate to in-doubt instead of risking a double-apply.

## Scripting scenarios by env

Both backends honor the same knobs, so a scenario reads identically at either fidelity:

- `INVESTIGATE_DEP_DOWN=1` — `reproduce` reports a broken environment (raises) → the step fails; clear it
  and resume to recover. (command-fails → fix → resume)
- `INVESTIGATE_CRASH_APPLY=1` — `apply_fix` hard-exits after writing, before journaling completion → the
  next run finds a dangling non-idempotent step and escalates to in-doubt.
- `INVESTIGATE_TRACE=path` — the flow's exported `observer` appends `(phase, key)` per step, so a driver
  can show what actually executed vs. what was served from the journal.

## Where to look

- Flow: `examples/investigate_repo.{py,js}` · Tools: `examples/investigate_tools.{py,js}`
- Narrated demo: `examples/walkthrough_investigate.py`
- Hermetic rungs: `inv_*` in `tests/run_ladder.py` (fixtures in `tests/fixtures/investigation/`)
- Real repo: `tests/run_integration.py` + `tests/fixtures/broken_project/`
