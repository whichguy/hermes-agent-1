# Roadmap — validate EVSI, then build the iterative context-builder

Driven by the benchmark (`benchmark-findings.md`). Sequence is deliberate: **get the rating right
before wrapping a loop around it.** Done first (cleanup): removed `answerability` (inert);
benchmark + conclusions saved.

## Phase 1 — Validate (and if needed, fix) EVSI

**Question:** does a high `value` actually predict a question whose answer improves the response?
The benchmark showed internal `value` and external quality can diverge (usaw: value 0.69, relevance
0.20). The EVSI *structure* is sound; the suspect links are input estimates, threshold scale, and
unproven validity.

**1.1 Use-relevant validity study (the core test).** For N prompts, produce three responses and
judge them blind for relevance to the prompt:
- `baseline` — respond with no clarification.
- `top2` — answer the **top-2** ranked questions, fold the answers in (evidence loop), then respond.
- `low2` — answer 2 **low-ranked / random** questions, fold in, then respond.
Pass condition: `top2 > low2 ≥ baseline`. If answering top-ranked questions doesn't beat answering
low-ranked ones, the rating isn't earning its place and we recalibrate/re-elicit. This study also
*builds the answerer + response-generator that Phase 2 needs* — so it de-risks the wrapper directly.

**1.2 Post-hoc formula ablations (near-free).** We already store per-question components (U, P, Δ,
stakes). Re-derive rankings under alternatives — EVSI-only (drop U), max-Δ vs P-weighted mean — and
see which best matches "which questions actually improved the response" from 1.1. No new model calls.

**1.3 Calibration → rank-relative (likely).** Absolute thresholds (0.40/0.60) are model-dependent
(fast → everything PRE_ANSWER; deepseek → fewer). Switch selection to rank/relative (top-K, or
≥ X% of the round's best). Small change, robust to model scale.

**1.4 Elicitation (only if 1.1 says inputs are the weak link).** Replace absolute 0–1 Δ/stakes with
**comparative/pairwise** judgments ("which answer would change the response more?") — models are far
better at comparisons than at calibrated absolute numbers. Bigger effort; gated on evidence.

**Exit:** an EVSI we've shown predicts realized improvement (or a recalibrated one that does).

## Phase 2 — The iterative context-builder ("iterate context")

A wrapper/orchestrator around the (validated) info-gain primitive that builds up context to
convergence and then responds. Keeps info-gain report-only; the wrapper does the answering + looping
+ responding (primitive-vs-orchestrator discipline).

**Loop:**
```
evidence = []                                   # accumulated "tombstones"
for round in range(max_rounds):
    ranked = infogain.run(prompt, cfg, evidence=evidence)      # rank questions given current context
    top = ranked.bucket[:K]                                     # K default 2
    if not top or top[0].value < diminishing_floor: break      # diminishing returns -> stop
    for q in top:
        ans = ANSWERER(q, prompt, evidence)                    # see fork below
        evidence.append(f"{q.question} -> {ans}")              # tombstone the resolved question
final = RESPONDER(prompt, evidence)                            # the payoff: best response to the prompt
return { final, evidence, trail }                              # trail = per-round questions/answers/value
```

**Components:**
- **ANSWERER = GROUNDED research (decided).** Each top question is answered by *real lookup*, not the
  model's own guess. Cleanest implementation that's self-contained and benchmarkable: reuse the `ask`
  skill's `model_utils.dispatch_single(model, "Research and answer concisely: <q> …", toolsets="web,file")`
  — a full Hermes agent run with web tools that returns a grounded answer. (We already depend on
  model_utils.) It returns `{answer, found}`; if research can't resolve the question, **don't tombstone
  it** — leave it open (an empirical version of "answerability"). Alternative backend = a direct
  web-search API if we want to skip the agent loop.
- **RESPONDER** — final step: "given the prompt + all established (researched) facts, produce the best
  response." Either a strong Ollama call or a `dispatch_single` with the enriched context.
- **CONVERGENCE** — stop when the round's top `value` < `diminishing_floor`, the bucket empties,
  `max_rounds` is hit, or top value < α × round-1 top (relative plateau).
- **Tombstones** = the resolved Q→answer facts, fed back via the existing `evidence` mechanism.

**Where it lives:** `scripts/iterate.py` in this skill (composes `infogain.run` + `dispatch_single`
research + responder); promote to its own `clarify-and-respond` skill if it grows. Info-gain stays
report-only.

**Cost reality (grounded changes this a lot).** The scoring is cheap Ollama (~14 calls/round, ~30s),
but each grounded answer is a **full agent-loop research call (~30–60s + web)**. K=2 × ~3 rounds = ~6
research calls ≈ 3–6 min/run, dominating cost. Implications to decide: cap rounds tightly, cache
researched facts, and make the diminishing-returns floor *aggressive* so we don't pay to research
low-value questions.

**Open knobs (to set during refine):** research model + toolsets · `diminishing_floor` · K per round ·
`max_rounds` · whether research that returns "not found" still counts toward convergence · final
deliverable = the response, or response + the researched evidence trail.
