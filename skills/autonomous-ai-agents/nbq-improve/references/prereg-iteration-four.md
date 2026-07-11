# NBQ pre-registration — iteration four (candidate 2 premise-test: answer-vs-assume ablation)

Filled instance of `preregistration-template.md`. Authored 2026-07-11 by the main loop BEFORE any
build. House rules bind: formula frozen; new arms off-by-default (existing arms + default arm set
byte-identical); the OBJECTIVE harness gates (this is an answering-side mechanism — how questions are
answered, not generated); gate + staged PROCEED/attribution-fail/NULL rule fixed before the run;
per-axis cost ceilings (verdict-rubric.md, banked iteration two); paired-design validity ENFORCED in
run (verdict-rubric.md §Paired-design). Baseline suite: 217 tests. All-deepseek/localhost.

Prior-lap SHA chain this learning builds on (git-history-is-the-log, verdict-rubric.md):
lap1 `9e15281fa`; lap2 `230599869`/`8165e32`; lap3 `1d0291b64`/`a52ac40`; policy `8d647141d`/`47df3b8`.

## Context — why this lap, why now

Three laps converged on one finding: **the valuable clarification questions are about INTENT** (which
reading, crash-vs-fallback, detail level), **not observable STATE.** Intent is unobservable by any
vantage/hop/investigator — answerable only by the user. Every answerability/reachability lever parked:
#30's self-rated multiplier (inert at 0.95), iteration two's retro probe (near-universal
unanswerability → no contrast), iteration three's reach→investigate arm (0/42 resolved). Banked route:
**candidate 2 — route intent questions to whoever holds the intent (a real user / the planner's
clarify loop).** This lap runs the CHEAP single-shot premise-test of candidate 2 in the objective
harness; the expensive relentless A/B is a *future* lap gated on this result.

## Hypothesis

- Candidate / backlog ID: candidate 2 (nbq→relentless clarify), premise-test stage.
- Hypothesis: giving the solver the ORACLE's real answer to nbq's high-EVSI questions beats giving it
  nbq's own ASSUMED default (`modal_answer`), holding the question set fixed — EVSI made objective,
  the single-shot core of candidate 2. AND the benefit is attributable to nbq's RANKING (answering
  HIGH-EVSI questions beats answering LOW-EVSI spec-answerable ones).
- Prior closed experiment affected: un-parks candidate 2 as the corpus route (iter3 backlog #3); does
  NOT re-open #30/candidate 3 (answerability lever stays a dead-end).
- Why now testable: the mechanism reduces to `modal_answer` (assume) vs `simulate_user` (answer) on a
  SHARED per-task question set — no new elicitation/generation, formula stays frozen. Applies lap
  three's banked paired-design lesson (shared question set), now *enforced* in-run.

## Expected mechanism

- Causal mechanism: nbq ranks a high-EVSI question because its default is uncertain and the stakes of
  guessing wrong are high. If that is real, feeding the solver the true answer (from the hidden spec,
  via `simulate_user`) should raise objective pass over feeding it nbq's guessed default
  (`modal_answer`), where the two differ (i.e. on tasks the oracle actually answers a top-K question).
- Observable consequence if correct: paired Δpass(answer − assume) > 0, concentrated on the
  clean-contrast subset (tasks with ≥1 `revealed=True` top-K question); and Δpass(answer − answer-lowevsi)
  > 0 (high-EVSI answers beat low-EVSI ones).
- Cheapest falsifying observation: answer ≈ assume (nbq's defaults were already right / questions
  don't matter single-shot), OR answer ≈ answer-lowevsi (any spec answer helps equally → the benefit
  is not attributable to nbq's ranking — a near-tautology, not nbq value).

### The four arms (share ONE nbq question set per task — paired-design ENFORCED)

Per task the nbq `bucket`/`all_scored` is generated ONCE and shared across all arms (fixes the exact
iter3 confound where `run_cell` regenerated questions per arm — the +0.100 gap was pure sampling
noise). **Matched injection phrasing:** every arm embeds its Q&A into the solver prompt with identical
wording; only the ANSWER CONTENT differs (guards the framing confound).

- `baseline` (existing): no questions.
- `assume` (NEW): each top-K nbq question "answered" with its `rec["modal_answer"]["answer"]` (nbq's
  guessed default) — models "nbq ranked it, but we assumed the default instead of asking."
- `answer` (NEW, shared set): each top-K nbq question answered by `simulate_user(hidden_spec, q)` — the
  intent oracle (real answer where the spec resolves, else "The spec doesn't say." → assumes).
- `answer-lowevsi` (NEW — the **nbq-ATTRIBUTION control**): oracle-answer the LOWEST-value
  spec-answerable questions (the `all_scored` tail) instead of the top-K. Guards the tautology: a
  benefit counts as nbq's only if answering HIGH-EVSI questions beats answering LOW-EVSI ones.

## Stage 0 — up-front power pre-check (read-only, ~free; a hard GO/NO-GO BEFORE the gate spends)

From the EXISTING `~/.hermes/outcome_eval_32.json` / `outcome_eval_iter3.json`, count per task from
the `nbq` rows: (a) how many top-K questions were `revealed=True` (oracle-answerable), (b)
`q_values` coverage/non-degeneracy (and, where available at build time, `modal_answer`
presence — flag questions lacking a usable default so `assume` never injects garbage).

- **Pre-registered contrast threshold (FIXED before any data read):** the harness is declared
  **structurally able** to test candidate 2 iff **≥ ⅓ of tasks have ≥1 revealed high-EVSI (top-K)
  question**. Below ⅓ ⇒ the answerable-high-EVSI contrast is too thin — declare the objective harness
  **structurally unable to test candidate 2 for this substrate**, do NOT run the 4-arm gate, pivot
  directly to the relentless headroom diagnostic, and journal the substrate-saturation finding.
- NO-GO is a legitimate, informative lap outcome (it catches substrate saturation before spending the
  gate). GO ⇒ proceed to the 4-arm gate below.

## Smoke test

- Command / procedure: run the three new arms on ONE task with a `revealed=True` top-K question; show
  `assume`, `answer`, `answer-lowevsi` inject visibly different answer content into the solver prompt
  for the SAME questions; confirm `baseline` + any existing arm (`nbq`) are byte-identical to a run
  without the new arms selected.
- Pass condition: the three arms differ only in injected answer content (matched scaffolding); the
  shared per-task question set is identical across arms; default/existing arms unchanged; zero new
  model calls when the new arms are not selected.
- Stop condition: the arms differ in prompt scaffolding beyond answer content (framing confound) ⇒
  STOP and fix the matched-phrasing helper; or the arms do NOT share the identical question set ⇒ STOP
  (paired-design violated — the run would be invalid).

## Targeted tests (offline, in `tests/run.py`)

- Tests: (1) **paired-design assertion** — all arms share the identical per-task question set (asserted
  in-run; a test drives a fixture task through the shared-generation path and checks arm question lists
  are identical); (2) **assume-arm content** — injects `modal_answer`, NOT the oracle answer; (3)
  **answer-lowevsi target** — draws from the low-value `all_scored` tail, not the top-K; (4)
  **matched-phrasing** — the arms' prompt scaffolding is identical modulo answer content; (5)
  **inert-by-default pin** — selecting no new arm / the existing arm set yields identical qa/scores and
  makes zero new model calls; (6) analysis-math unit test (paired Δ, wins/losses, SE, correlation on a
  toy fixture).
- Inert-by-default pin: REQUIRED.
- Required assertions: paired-design identity across arms; assume≠oracle; low-EVSI tail targeting;
  matched scaffolding; default byte-identity + zero new calls when unselected.

## Gate (arms, n, primary metric)

- Control arms: `assume` and `answer-lowevsi` (the two contrasts `answer` must beat).
- Experimental arm: `answer`.
- Also run: `baseline` (paired denominator / secondary deltas).
- Paired sample and n: n=34 (both banks), K=3, all-deepseek, `--strict-preflight`, cost columns on
  (4 arms ⇒ ~4/3× the per-task cost of a 3-arm run; report per-axis).
- **Primary metric — BOTH must hold for PROCEED:**
  1. paired **Δpass(answer − assume) > 0** with **wins ≥ 2× losses** AND **mean clears its SE** —
     asking beats nbq's own default; AND
  2. paired **Δpass(answer − answer-lowevsi) > 0** with the same guard — the benefit is attributable
     to nbq's RANKING, not to answering any spec-resolvable question.
- Secondary diagnostics:
  1. Δpass(answer − baseline), Δpass(assume − baseline) — does assuming help at all? does answering add?
  2. **Clean-contrast subset:** the primary deltas restricted to tasks where the oracle actually
     answered (`revealed=True`) a high-EVSI question — where the arms genuinely differ (guards the
     null-bias from the ~79–81% unanswerable mass).
  3. **EVSI-validation product (journaled regardless of verdict):** correlation of nbq's predicted
     `evsi`/`value` with realized per-question Δ(answer − assume). Honest caveat baked into the
     reading: this Δ ≈ P(nbq's default wrong)·value, which nbq itself estimates as `(1−modal_prob)·evsi`
     — on its own it partly *recovers nbq's calibration* rather than proving external value; the
     `answer-lowevsi` control is what makes a positive nbq-attributable. Treat as a calibration check,
     not proof of external value.
- **Paired-design validity assertion (lap-three lesson, enforced):** the run asserts all arms share
  the identical per-task question set; if not, the run is INVALID (the exact defect that made
  iteration three's +0.100 arm gap pure sampling noise).

## Mechanical staged gate (frozen before build; honored in commit order)

Apply `verdict-rubric.md` (broad-win guard, mean>SE, cost-ceiling veto) then this staged rule:

- **PROCEED to Stage 2 (the expensive relentless A/B — a FUTURE lap)** iff BOTH primary conditions
  hold: Δpass(answer − assume) > 0 AND Δpass(answer − answer-lowevsi) > 0, each with wins ≥ 2× losses
  AND mean-clears-SE. Read as **premise-*consistent* and nbq-attributable — worth the relentless
  test**, NOT "candidate 2 proven" (single-shot value ≠ multi-cycle thrash reduction).
- **answer beats assume but NOT answer-lowevsi → attribution FAILS.** Answering helps, but nbq's
  ranking doesn't pick better questions than the tail. That is an nbq-value finding (log it); it does
  NOT greenlight the expensive build — it re-opens the ranker, not candidate 2's integration.
- **NULL / negative → do NOT build the relentless A/B.** Pre-registered caveat: a single-shot null is
  **not decisive** (candidate 2's mechanism is multi-cycle thrash-avoidance this harness can't see,
  and the substrate is null-biased). Route to the **headroom diagnostic** (a few live relentless runs
  confirming high-EVSI intent questions get `via:"assumed"` today) — it does NOT kill candidate 2.
- Disposition of the arm code either way: it ships as a standing **opt-in eval instrument** (new
  answer-vs-assume ablation), NOT a default flip — the formula/lenses are untouched.

## Efficiency budget (per-axis ceilings — verdict-rubric.md)

- Expected Δcalls: `assume` adds 0 model calls beyond question generation (default is read from the
  `rec`); `answer` / `answer-lowevsi` add ≤ K `simulate_user` calls per task (same call class the
  existing `nbq` arm already makes). Four arms ≈ 4/3× a 3-arm run.
- Expected Δtokens / Δwall: bounded by those extra calls; localhost/deepseek (~seconds/task) keeps it
  modest. The exact wall for the shared-question 4-arm run is unmeasured — reported per-axis in the
  findings.
- Ceiling that vetoes even a result win: this is an eval INSTRUMENT (no default flip), so no per-axis
  ceiling vetoes the *instrument* — but the per-axis cost (wall / tokens / calls) is REPORTED in the
  findings so a future relentless integration inherits a real budget. A future shipped integration
  busting wall ≤ 10% / tokens ≤ 15% / calls ≤ +K is adopt-with-knob-off or no-adopt.
- If exceeded: N/A for the instrument; binding on the future integration (adopt-with-knob-off).

## Rollback (selector + flag)

- Selector: the new arm names `assume` / `answer` / `answer-lowevsi` (absent ⇒ never run; default arm
  set unchanged). The shared-question-generation refactor is behavior-preserving for existing arms.
- Rollback flag / value: don't select the new arms / remove them from the arm list.
- Absent-key behavior: existing arms + default arm set run byte-identical; zero new model calls.

## Journal stubs for BOTH outcomes

### If PROCEED (premise-consistent + nbq-attributable)

> **[candidate 2 premise-test] — PROCEED.** Δpass(answer − assume) = ___ (wins ___/losses ___,
> clears SE), Δpass(answer − answer-lowevsi) = ___ (wins ___/losses ___, clears SE) at n=___. Clean
> contrast subset (n=___): ___. EVSI↔realized-Δ ρ = ___. Δcost: wall ___, tokens ___, calls ___.
> Reading: single-shot value is premise-consistent AND attributable to nbq's ranking → the expensive
> relentless A/B is worth building (Stage 2, queued as a future lap). NOT "candidate 2 proven."
> Evidence: `~/.hermes/<result>.json`.

### If attribution FAILS (answer > assume, answer ≤ answer-lowevsi)

> **[candidate 2 premise-test] — ATTRIBUTION FAILS.** Δpass(answer − assume) = ___ but
> Δpass(answer − answer-lowevsi) = ___ (not > 0 / not broad). Answering spec-resolvable questions
> helps, but nbq's ranking does not pick better questions than the low-value tail. This re-opens the
> ranker (an nbq-value finding), NOT candidate 2's integration — the relentless A/B is NOT greenlit.
> EVSI↔realized-Δ ρ = ___. Δcost: ___. Evidence: ___.

### If NULL / negative

> **[candidate 2 premise-test] — NULL (non-decisive).** Δpass(answer − assume) = ___ (≤0 / within
> noise) at n=___; clean-contrast subset ___. Pre-registered caveat: a single-shot null is NOT
> decisive — candidate 2's mechanism is multi-cycle thrash-avoidance this harness cannot see, and the
> substrate is null-biased (~79–81% unanswerable). The relentless A/B is NOT built off a null; route
> to the **headroom diagnostic** (confirm high-EVSI intent questions get `via:"assumed"` in live
> relentless runs). Candidate 2 is NOT killed. EVSI↔realized-Δ ρ = ___. Δcost: ___. Evidence: ___.

### If Stage 0 NO-GO (substrate saturation)

> **[candidate 2 premise-test] — STAGE 0 NO-GO.** Only ___/___ tasks (<⅓) have ≥1 revealed high-EVSI
> question — the answerable-high-EVSI contrast is too thin for the objective harness to test candidate
> 2. The 4-arm gate was NOT run (no live spend). Finding: the objective/micro-agentic substrate is
> saturated for answerability-class levers; the honest next step is the relentless diagnostic, not a
> fourth null-biased harness lap. Candidate 2 route unchanged. Evidence: Stage-0 pre-check output.
