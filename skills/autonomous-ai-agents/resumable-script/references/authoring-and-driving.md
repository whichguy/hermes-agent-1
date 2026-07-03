# Driving a run (for an LLM orchestrator)

Your job as the driver: **run the flow, surface its intervention requests to the user, and
resume by passing the answer back.** This loop is identical whether the flow is the product (a
workflow **spec** — authoring guide: `references/workflow.md`) or a substrate code-first flow
file (authoring appendix: `references/authoring-flows.md`). Exit codes: `cli-contract.md`;
on-disk format: `journal-format.md`.

## Mental model (read this first)

**The journal on disk IS the state.** You never serialize or pass back a state blob. To resume
you pass back only the **answer** to the pending question, plus the **same `--state-dir`**. The
engine re-runs the flow from the top; completed steps replay from the journal instantly, so only
new work runs.

## 1. The loop you run

```
state_dir = "<dir>"                                   # pick once; REUSE it for every resume
out = engine run --flow F --state-dir state_dir --input '<json>'
while out.status == "suspended":
    answer = ask_the_user(out.pending.question)       # or auto-resolve (headless)
    out = engine resume --flow F --state-dir state_dir --answer '<answer>'
# out.status is now "completed" (use out.result), "failed", or "in_doubt"
```

Each invocation prints **one JSON line** (parse the last stdout line) and sets an exit code:

| stdout | exit | meaning |
|---|---|---|
| `{"status":"completed","result":...}` | 0 | done |
| `{"status":"suspended","pending":{"key","question","schema"}}` | 10 | needs intervention |
| `{"status":"in_doubt","pending":{...,"options":["completed","retry","abort"]}}` | 11 | a non-idempotent step was interrupted; resolve with `resume --resolve <option>` (see `cli-contract.md` §In-doubt) |
| `{"status":"failed","error":{"name","message","step"?,"attempts"?}}` | 1 | terminal failure (`step`/`attempts` = which step, after how many tries — absent for glue errors) |

On any exit other than 0/10, stop and walk **`references/driving-failures.md`** — the failure
decision tree (keyed by exit code + stderr) that tells you what to inspect and exactly what to do.

## 2. How the script asks for intervention

When the flow reaches a gate (`decide` state / escalation / `ctx.ask`), the **run exits 10** and
prints `pending`:
- `pending.key` — which gate (you don't usually need it; `resume` finds the latest open gate).
- `pending.question` — the **author-defined** payload to show the user (e.g. `{"prompt": "...", "options": [...]}`).
- `pending.schema` — optional `{type, enum?, default?}`.

Authors: design `question` so a human (or the driver) can answer it, and set `schema.default` so
a **headless** run (`--auto` / `HERMES_HEADLESS=1`) can proceed without a human.

## 3. How to resume — passing the answer back

```
engine resume --flow F --state-dir <same dir> --answer '<json-or-text>'
```
- **JSON answer** (`true`, `'"pro"'`, `42`, `'{"x":1}'`) → used as-is.
- **Free-form text** (`'yeah go ahead'`) → routed through the flow's `interpreter` hook into the
  schema value (journaled as the structured answer, so replay never re-calls the LLM).
- No `--input` on resume — the original input is read from the journal.
- The engine appends `ask_answered`, re-runs the flow, replays completed steps, returns the answer
  at the gate, and continues. It may **suspend again** at the next gate → repeat the loop.

## 4. Worked round-trip

```
$ engine run --flow triage.py --state-dir /tmp/run1 --input '{"customer":"acme","topic":"widget","items":[...]}'
{"status":"suspended","pending":{"key":"approve#0","question":{"prompt":"Approve a 140 refund for acme?","options":["approve","deny"]},...}}   # exit 10
        # journal now holds: research (done), per-item summaries (done), assess (done), approve (asked)

$ engine resume --flow triage.py --state-dir /tmp/run1 --answer '"approve"'
{"status":"completed","result":{...}}                                                                                                          # exit 0
        # on resume: research & summaries REPLAY (side effects do NOT re-fire); notify is the only new work
```

Free-form variant: `--answer 'go ahead'` → the `interpreter` hook maps it to the schema value;
the structured answer is journaled, so any later replay is deterministic and LLM-free.
