# NBQ improvement backlog

This is a living ranked list. Rank by expected value ÷ evaluation cost, highest first.

1. **#32 First-order candidate source — iteration one, IN PROGRESS (gate pending).** Hypothesis: the
   P4 gap is generation altitude; inject a naive "K best clarifying questions" call as round-1
   candidates (lens `firstorder`), scored by the normal pipeline.
2. **#33 Discrimination preflight — iteration one, instrument (audit A7).** Verify judge/elicit
   models can actually tell better from worse, not just answer the question at all.
3. **#30 answerability weighting.** Re-open ONLY post-#32, only IF the unanswerable rate measured
   there is > 50%, and only with a mechanism that is NOT self-rated. The old multiplier was inert at
   0.95 in 15/16 cells; a self-rated mechanism already failed once.
4. **Prompt distillation — blocked on the #32 verdict.** The certified-prompt path is attractive for
   latency but should not be pursued until #32 resolves whether generation altitude is really the
   gap.

## Agentic-workflow integration candidates

The RESEARCH step (step 2, run by the main loop each iteration) populates this section with ranked
candidates for where `next-best-questions` should hook into planner/executor loops:
`relentless-solve`'s clarify step, `investigator` routing, and `task-decomposer`.

Ranked by expected value ÷ evaluation cost (iteration-one sweep, 2026-07-04). Sources:
Clarifier→Planner→Implementor (EMNLP 2025 industry 163), QualityFlow (verifier-gated clarify
branch), DenoiseFlow (arXiv:2603.00532, sense/propagate/control semantic uncertainty), adaptive
planning-horizon (arXiv:2605.08477), Routine (arXiv:2507.14447).

1. **[HIGH EV/cost] Residual-question ↔ failed-path correlation (uses EXISTING logs, zero build).**
   Hypothesis: skipped high-EVSI (assume-default) questions predict downstream execution failures.
   Mechanism: `relentless-solve`'s `journey.json` already records failed-paths-as-evidence; join
   nbq's per-task assume-default annotations against those failures. Cheapest falsifying test: retro
   analysis of existing journey logs — if no correlation, the "clarify earlier" thesis is falsified
   for free. Feeds candidates 2 and #30.
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
