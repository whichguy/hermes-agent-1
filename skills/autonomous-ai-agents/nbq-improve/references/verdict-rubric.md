# Verdict rubric

Apply these rules mechanically to the pre-registered gate.

- **Broad-win guard:** Wins must be ≥ 2× losses across the gate's paired comparisons. Otherwise the
  verdict is no-adopt.
- **Mean > SE:** Do not move a default on a within-noise ablation win. The effect must clear its own
  standard error.
- **Borderline = keep (no-adopt):** Follow the #28 precedent: a directionally right result that is
  not a broad win stays off.
- **Cost-ceiling veto:** A result win that exceeds the pre-registered efficiency budget is no-adopt,
  or ships as adopt-with-knob-off-by-default when that disposition was pre-registered. Cost is
  **multi-dimensional**: pre-register a ceiling for EACH axis — wall, tokens, and calls — and treat a
  bust on ANY one axis as a veto. Do not collapse cost to a single scalar (a wall-neutral but
  token-heavy change, or one that adds a hidden extra model call, must not pass on wall alone).
- **Proxy-vs-objective gate selection:** Use the OBJECTIVE outcome harness (`outcome_eval.py`-class
  ground truth) for anything touching elicitation or generation. Use the #25-style two-arm realized
  scan for exposure/lens-only changes.
- **Paired-design validity for answering-side mechanisms:** when the treatment changes how questions
  are ANSWERED (not generated), the control and treatment arms MUST share the SAME generated question
  set — vary only the answering. Two arms that each re-generate questions stochastically are UNPAIRED;
  their pass/frac delta confounds the mechanism with question-sampling variance and cannot be read as
  a treatment effect. Verify the arms shared questions before trusting a paired Δ (iter-three
  reach→investigate: 0/14 tasks shared questions, so a +0.100 arm gap was pure sampling noise on top
  of a mechanism that fired 0 times).

## Commit-message contract

Every iteration commit MUST include sections named `ATTEMPTED`, `WHY`, and `RESULT`. In a verdict
commit, `RESULT` must include the actual gate numbers—not only a verdict word—plus Δresult, Δcost,
and the verdict. A build-stage, pre-gate commit may put `build stage, gate pending` in `RESULT`, but
it must be followed by a separate verdict commit after the gate runs.

**Git history IS the experiment log.** Every lap commit also cites the KEY GIT REFERENCES its
learning chains from — the SHAs of the prior laps/experiments whose findings this lap builds on — so
the history reads as a self-contained learning chain without needing the docs. This holds **even when
the experimental code is not kept**: a discarded, reverted, or never-shipped experiment STILL gets a
commit that preserves its thesis (`ATTEMPTED`/`WHY`) and lesson (`RESULT`), plus a reference to what
was removed and why. A negative result must never vanish because its code didn't ship — the code is
optional, the logged learning and its provenance are not.
