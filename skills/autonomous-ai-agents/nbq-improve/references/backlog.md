# NBQ improvement backlog

This is a living ranked list. Rank by expected value ÷ evaluation cost, highest first.

0. **[NEW TOP — iteration five] `questions-only` arm gate (the exposure hypothesis).** Backed by the
   iter-4 retro addendum (post-hoc, `evsi-validation-findings.md` §Retro addendum): on the 20 tasks
   where the oracle revealed NOTHING, the `answer` arm injected pure refusals yet still beat baseline
   +0.143 and beat `assume` +0.077 — surfacing nbq's top-K QUESTIONS as explicit unknowns appears to
   carry most of the single-shot value; the oracle answers add little (+0.181 on revealed tasks).
   **Pre-register** (per house rules; this is NOT yet greenlit to build) a fifth arm: inject nbq's
   top-K questions as explicit "UNKNOWN — handle sensibly", **zero oracle calls**, shared-question
   paired design (reuse `--paired-ablation`'s one-generation-per-task substrate). Primary:
   Δpass(questions-only − baseline) > 0 with wins ≥ 2× losses AND mean-clears-SE (does exposure alone
   reproduce the answer arm's gain?) and Δpass(answer − questions-only) (does oracle content add
   anything beyond exposure?). Cheap: no new model calls beyond the shared generation + one solve/arm.
   **This also reshapes the queued headroom diagnostic (item below):** if exposure is the active
   ingredient, the live-relentless check must confirm relentless SURFACES the unknowns to the planner
   (as explicit open questions), not merely that high-EVSI questions get `via:"assumed"`. Caveat
   inherited: the retro finding conditions on oracle behavior (selection effects); the pre-registered
   arm is what can turn it into a verdict.
1. **#30 answerability weighting — PARKED AGAIN 2026-07-04 (iteration two): premise falsified for
   free.** The re-open condition (unanswerable 77% > 50%) was met, but a zero-cost retro probe of the
   objective corpus did NOT support #30's premise ("kept high-EVSI unanswerable questions cause
   objective failure"): highest-EVSI-unanswerable × fail r=+0.05 (within SE), any-unanswerable × fail
   degenerate (97% base rate) + wrong-direction. Unanswerability is near-universal in this corpus, so
   there is no answerable-question contrast to steer toward. **Re-open condition (new):** a
   higher-contrast answerability corpus exists (built via candidate 2 or 3 below) AND a non-self-rated
   mechanism is pre-registered (the batched strict-simulator answer/refuse probe is designed + parked
   in `next-best-questions/references/prereg-iteration-two.md` item B, unexercised). Full verdict:
   `next-best-questions/references/{evsi-validation-findings.md,design-decisions.md}` §Answerability.
2. **Candidate 3 (reach→investigate loop) — CLOSED 2026-07-04 (iteration three): NO ADOPT.** Built an
   opt-in `nbq-reach-investigate` arm (fixture-aware mock investigator, leakage-guarded). Gate
   (agentic n=14, deepseek): **0 questions resolved of 42** — nbq's high-EVSI questions are about
   *intent* (which reading, crash-vs-fallback, detail level), and an investigator observes *state*,
   not intent. Unanswerable rose (+2.4pts); the +0.100 arm gap was unpaired sampling noise (0/14
   tasks shared questions). **Finding that reframes the program:** the answerability/reachability
   lever is a likely dead-end — the value IS in the unobservable intent questions; intent is
   answerable only by the USER. Full verdict:
   `next-best-questions/references/{evsi-validation-findings.md,design-decisions.md}` §Reach→investigate.
3. **[STILL TOP] Candidate 2 — nbq→relentless clarify. Stage-1 premise-test DONE 2026-07-11
   (iteration four): ATTRIBUTION_FAIL; Stage 2 (the relentless A/B) NOT greenlit; next = the
   relentless headroom diagnostic.** The cheap single-shot ablation (4 arms, ONE shared question set
   per task, paired design enforced in-run; prereg `prereg-iteration-four.md` @ 823e12786):
   Δpass(answer − assume) = +0.093 (9W/3L, clears SE, broad win) — real answers DO beat nbq's assumed
   defaults — but Δpass(answer − answer-lowevsi) = −0.024 (6W/9L): the low-value tail helped at least
   as much, so the benefit is not attributable to nbq's ranking ON THIS SUBSTRATE. Mechanism measured:
   the spec-bound oracle reveals 19% of top-K vs 33% of tail — nbq ranks intent questions the oracle
   refuses; the harness rewards spec-answerable trivia (the pre-registered near-tautology). Candidate
   2 neither proven nor killed. **Queued next: the headroom diagnostic** — a few live relentless runs
   confirming high-EVSI intent questions get `via:"assumed"` today (cheap, observational); the
   expensive A/B only if headroom is real AND a non-spec-bound intent oracle (a real user / the
   planner's clarify loop) anchors the outcome. Verdict:
   `next-best-questions/references/{evsi-validation-findings.md,design-decisions.md}`
   §Answer-vs-assume paired ablation. Instrument kept: `--paired-ablation` + `stage0_precheck.py` +
   `analyze_ablation.py`.
4. **Retro answerability probe (candidate 1) — DONE 2026-07-04: PARK verdict delivered.** Built
   `evals/probe_answerability.py` (offline, deterministic, stdlib-only; `--arm` added iter 3) + tests;
   ran on the pinned objective corpus. Zero new model calls. Product: parked #30 for free and
   diagnosed *why* (near-universal unanswerability). Standing instrument. See candidate 1 below.
3. **#32 First-order candidate source — CLOSED 2026-07-04: NO ADOPT.** Built, off-by-default
   (`--firstorder`). Gate (n=34, all-deepseek): paired vs nbq Δ+0.049 but 6W/6L (broad-win guard
   fails), unanswerable 77%, a lens-payoff regression, and +16.8% wall (> 10% ceiling). Altitude has
   signal (mean beat plain nbq) but did not close the gap — zeroshot still won +0.274 (15W/1L,
   p=0.0005). Re-open only with a NEW hypothesis about *why* altitude-in-the-pipeline underperforms
   (candidate: the pipeline demotes the first-order candidates — inspect their per-stage P/Δ/stakes).
   Full verdict: `next-best-questions/references/design-decisions.md` §First-order candidate source.
4. **#33 Discrimination preflight — CLOSED 2026-07-04: ADOPTED (opt-in instrument).** `--strict-preflight`;
   live check fast 8/8, deepseek 8/8. No default flip (8 calls/model, off by default).
5. **Prompt distillation — unblocked by the #32 no-adopt (the value model is not the gap), lower
   priority than #30.** Re-scope the certified-prompt path against whatever answerability mechanism
   #30 lands.

## Iteration-five queue (each needs its own pre-registration per house rules)

Seeded by the iteration-four verdict + retro addendum (2026-07-11). Ranked; the `questions-only` arm
(item 0 above) is the lap-five lead.

1. **Headroom diagnostic — RESHAPED by the exposure finding.** Original: a few live relentless runs
   confirming high-EVSI intent questions get `via:"assumed"` today. Reshaped: also confirm whether
   relentless SURFACES those unknowns to the planner (renders them as explicit open questions), not
   just that they are silently assumed. If exposure (item 0) is the active ingredient, "surfaced vs
   swallowed" is the load-bearing distinction, not `via:"assumed"` prevalence. Cheap, observational.
2. **Matched-answerability attribution control.** `answer-lowevsi` conflates ranking quality with
   answerability (the tail is more spec-answerable, so it wins for a reason orthogonal to nbq's
   ranking). Design an arm that holds answerability fixed across the compared question sets (e.g.
   match top-K vs tail on oracle-reveal rate before comparing), so a future attribution test measures
   ranking, not answerability.
3. **`--reps` + paired seeds for power.** iter-4 primary was single-rep at n=34 (sign_p 0.146; many
   ties). Add repetition support (paired seeds, aggregate across reps) so a future gate can clear
   significance rather than reporting directional-only deltas.
4. **Substrate redesign (spec ⊃ tests / intent-holding persona oracle).** The hidden-spec oracle
   cannot reward intent questions (the spec rarely encodes intent), so this substrate structurally
   cannot attribute nbq's value. Options: a hidden_spec that strictly supersets the visible tests, or
   an intent-holding persona oracle decoupled from the objective checks. Build only if the candidate-2
   line still needs the objective harness after iteration five.
5. **Installed-twin reconciliation.** The installed tree
   (`~/.hermes/skills/autonomous-ai-agents/...`) carries a pre-existing `test_infogain` failure
   (17F/2E) against its OWN pairwise modules that pre-dates this lap — reconcile the installed twin
   against canonical (it is out of scope for the iter-4 lap's `test_outcome_eval` sync).
6. **Protocol substrate-exit criterion (nbq-improve SKILL edit).** After N consecutive laps where the
   harness structurally CANNOT observe the mechanism under test (laps 1–4 all hit intent≠state /
   substrate saturation), the protocol should require the NEXT lap to change SUBSTRATE, not just the
   lever. This is a `nbq-improve/SKILL.md` edit → bumps nbq-improve's own version when it lands.

## Agentic-workflow integration candidates

The RESEARCH step (step 2, run by the main loop each iteration) populates this section with ranked
candidates for where `next-best-questions` should hook into planner/executor loops:
`relentless-solve`'s clarify step, `investigator` routing, and `task-decomposer`.

Ranked by expected value ÷ evaluation cost (iteration-one sweep, 2026-07-04). Sources:
Clarifier→Planner→Implementor (EMNLP 2025 industry 163), QualityFlow (verifier-gated clarify
branch), DenoiseFlow (arXiv:2603.00532, sense/propagate/control semantic uncertainty), adaptive
planning-horizon (arXiv:2605.08477), Routine (arXiv:2507.14447).

1. **[HIGH EV/cost] Retro answerability↔objective-failure probe (inside `outcome_eval`, zero new
   model calls). DRIFT-CORRECTED 2026-07-04 (iteration two REVIEW).** The originally-planned join
   against `relentless`'s `journey.json` is NOT runnable: the only journey.json files present are the
   three relentless smoke fixtures, and none carry nbq assume-default annotations (that annotation is
   the *unbuilt* candidate 2 integration — so the join key does not exist yet). The genuinely cheapest
   falsifying test lives in the OBJECTIVE harness output instead: `~/.hermes/outcome_eval_32.json`
   already carries, per task/arm, `meta.q_values` (per-question EVSI, index-aligned), `qa[i].revealed`
   + `qa[i].answer` (answerability signal — "The spec doesn't say." = unanswerable), and `frac` /
   `per_test` (objective outcome). Hypothesis: tasks whose kept high-EVSI questions were *unanswerable*
   fail the objective outcome more often than tasks whose high-EVSI questions were answerable.
   Falsifying result (no correlation, or unanswerable↔success) → #30's "answerability causes failure"
   thesis is falsified for free → park #30, re-scope. Feeds #30. The journey.json join is re-parked
   behind candidate 2's (unbuilt) nbq→relentless integration.
2. **[HIGH] nbq as the Clarifier preflight into `relentless-solve`'s clarify step (EMNLP pattern).**
   Hypothesis: EVSI-ranked pre-plan clarification reduces replans / failed paths vs unstructured
   clarify. Mechanism: wire nbq's top-K bucket as the Clarifier's critical Q-A pairs feeding the
   planner. Cheapest test: A/B a small relentless task set with vs without the nbq preflight, primary
   metric = replan count / journey failed-path count (a workflow-integration change, not an
   elicitation change → two-arm, not the objective harness). Cost: needs the relentless harness.
3. **[MED-HIGH] Reach→investigate→evidence loop (ties reach lens #29 to `investigator` routing).**
   Hypothesis: routing reach questions to the investigator (execute the hop, return the observable
   as evidence, re-run nbq) resolves "unanswerable" questions that today inflate the unanswerable
   rate. Cheapest test: on access/systems tasks, measure unanswerable-rate drop when reach questions
   are answered by a (mocked) investigator returning the observable. Connects to #30 answerability.
4. **[MED] Mid-execution re-clarify trigger (QualityFlow / adaptive-horizon).**
   Hypothesis: clarification value peaks at replan boundaries where a plan just failed, beating
   up-front-only clarification. Mechanism: expose nbq as the "clarify" branch of a verifier choosing
   submit/clarify/revert/continue at replan nodes. Cheapest test: overlaps candidate 1 — measure
   whether residual high-value questions cluster on the tasks that hit a replan. Gate the retro
   analysis before any real integration.
5. **[MED] Uncertainty propagation into execution (DenoiseFlow).**
   Hypothesis: propagating each kept question's assume-default risk (already rendered as "~X% chance
   that's off in a way that matters") forward as an executor caution signal reduces silent
   wrong-output. Cheapest test: instrument the existing assume-default annotations; check whether
   they flag the tasks that later fail an objective check. Formula-frozen (report annotation only).

## Parked (with re-open conditions)

- **A1:** parked audit item; re-open condition: TBD on next review.
- **A2:** parked audit item; re-open condition: TBD on next review.
- **A10:** parked audit item; re-open condition: TBD on next review.
- **M2:** parked audit item; re-open condition: TBD on next review.
