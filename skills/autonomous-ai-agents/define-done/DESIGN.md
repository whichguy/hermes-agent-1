# define-done — DESIGN (v0, 2026-07-01)

*Family-wide roles, state contracts, call graph, and isolation rules: `skills/ARCHITECTURE.md`.*

Given a prompt whose solution implies a set of activities, compile the **requirements /
definition-of-done decomposition**: grouped, itemized, logically ordered *outcomes* that
must hold for the prompt to be satisfied — explicitly WHAT, never HOW. This extracts and
deepens the framing that method-explorer's P0 compresses into two lines (`INTENT:` +
one-line `SUCCESS:`) into a durable, reviewable, amendable artifact.

## The artifact

`${HERMES_HOME}/specs/<slug>/dod.md` — single writer (the specifier/amender LLM), read by
the pure parser `scripts/spec.py`. Grammar (exactly this shape):

```
# DoD: <slug>   STATE: draft | agreed | satisfied
INTENT: <one sentence — immutable>
HARD (inviolable): <list>
SOFT (relaxable, ranked): 1) ...  2) ...

REQUIREMENTS   (markers: ○ unmet · ✓ met (receipt) · ~ waived (receipted reason))
- R1   <group: outcome that must hold>              [after: —]
  - R1.1  <itemized requirement>   check: cmd — <command that proves it>    ○
  - R1.2  <itemized requirement>   check: judge — <observable criterion>    ○
  - R1.3  <itemized requirement>                                            ○
- R2   <group>                                       [after: R1]
OPEN: <ambiguities the decomposition could not resolve>
AMENDMENTS:
- <cycle> <R-id> <added|waived|split> — <one-line reason>
```

## The world-state test (the core rule)

Every item must be phrased as a condition that IS TRUE at the end, checkable by
observation — never an activity. "Run the migration" FAILS the test; "every row in X has
non-null Y" PASSES. `[after:]` encodes logical dependency (this must hold before that
*can* hold), not execution order. The linter warns on imperative-verb leaves without a
check clause (method smell).

## Locked decisions (jim, 2026-07-01)

1. **Checks are optional per leaf** (`check: cmd — <command>` mechanical · `check: judge
   — <criterion>` observable · bare). Honesty moves to satisfaction time: a bare or
   judged item may be marked ✓ ONLY with a receipt (the linter errors on receipt-less
   ✓/~). A requirement without a check is allowed; an unreceipted claim is not.
2. **INTENT is immutable.** Requirements amend only via receipted AMENDMENTS entries
   (add / waive `~` / split) — mirrors soft-constraint relaxation. Evidence from failed
   cycles reshapes the spec without silently dropping the goal.
3. **Standalone v1.** Envelope + parser + linter + fixtures + contract tests. No edits
   to relentless-solve / method-explorer / resumable-script in this pass.

## State-transformer profile

- **Specifier** (LLM, via `spec_envelope.spec_prompt`): prompt → dod.md `STATE: draft`.
- **Review gate** (human or caller policy): draft → `agreed`.
- **Checker** (future, code + judge pass): world + dod → per-R ✓/○ → `satisfied`.
Durable single-writer artifact + pinned grammar + pure parser: the same seam pattern as
method-explorer's plan-tree (envelope instructs, parser reads, contract tests pin the
two together — the GUARD-HALT lesson).

## Integration map

- **task-decomposer** (LIVE, 2026-07-02): the consumption seam. The driver passes
  `unmet(parse_dod(...))` ids to `envelope.plan_prompt(..., dod_ids=...)`; every plan
  task carries `serves: [R-ids]`, `planfile.coverage_violations` makes coverage binding,
  and `report.completion_report(..., dod_parsed=...)` rolls tasks up into per-requirement
  met/blocked/pending/waived. The dod travels as `parse_dod()`'s dict — task-decomposer
  never imports this skill (pinned by its `tests/test_contracts.py`).
  (This subsumed the short-lived `intent-to-tasks` sibling, retired 2026-07-02: its
  taskmap.md duplicated task-decomposer's plan-as-data role; `serves:`/coverage/the
  completion contract moved there.)
- **relentless-solve** (LIVE, 2026-07-02): `--dod path/to/dod.md` threads the spec into
  the full route — `render()` appends the unmet requirements, plans are coverage-checked,
  and each cycle lands a `c<N>/report.json` completion report.
- Deferred: SUCCESS authority upgrading from "final verification task worked" to "all
  R's ✓ with receipts" (a checker that writes receipts back into dod.md); `OPEN:` items
  seeding the investigator round (an ambiguous requirement changes the whole plan —
  exactly what EVSI weights highest); method-explorer P0 *consuming* INTENT/HARD/SOFT
  from the spec instead of inventing them; the router sizing the route from spec shape
  (one group, one leaf → single_method).
