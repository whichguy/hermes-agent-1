# NBQ pre-registration — iteration three (candidate 3: reach→investigate, mocked)

Filled instance of `preregistration-template.md`. Authored 2026-07-04 by the main loop BEFORE any
build. House rules bind: formula frozen; feature off-by-default behind a selector; OBJECTIVE harness
gates (this is an answering/resolution change); gate + adopt rule fixed before the run; per-axis cost
ceilings (verdict-rubric.md, banked iteration two). Baseline suite: 211 tests. Host reaches
`deepseek-v4-pro:cloud` (verified 2026-07-04 — the old "host can't reach :cloud" note is stale).

Follows iteration two's PARK of #30: the retro probe showed unanswerability is near-universal in the
objective corpus because the strict simulator answers ONLY from `hidden_spec`
(`outcome_eval.py:simulate_user`, "The spec doesn't say." ⇒ `revealed=False`). Reach questions (#29)
are *structurally* spec-unanswerable-yet-investigation-answerable ("does a reachable vantage turn an
unknown into an observable"). This lap tests candidate 3: does resolving those questions from
observable state (what a real investigator's hop would see) lift objective pass AND create the
answerability↔pass contrast #30 needs?

## Hypothesis
- Candidate / backlog ID: candidate 3 (reach→investigate loop); un-blocks #30.
- Hypothesis: on the agentic/access bank, resolving strict-unanswerable questions via a fixture-aware
  mock investigator (observable state, not spec-only) raises paired objective pass vs `nbq`, AND
  produces a non-degenerate answerability↔pass signal (the higher-contrast corpus #30 was missing).
- Prior affected: #30 (parked iteration two on a degenerate/near-constant answerability predictor).
- Why now testable: the mock converts some unanswerables into answerables → answerable/unanswerable
  contrast on the SAME tasks → the iteration-two probe becomes informative.

## Expected mechanism
- New OPT-IN arm `nbq-reach-investigate` in `outcome_eval.py`. It generates questions exactly like the
  `nbq` arm, then for each question the strict simulator marks unanswerable it calls a
  **mock investigator**: a simulator given the task's OBSERVABLE state (hidden_spec + the materialized
  fixture / environment — files, env vars, categories) with a permissive directive: "you can observe
  the environment; answer from spec OR from observable environment state; do NOT invent; refuse only
  if genuinely unobservable." Resolved ⇒ `revealed=True` with the observed answer; else stays
  unanswerable. Existing arms are byte-identical (arm runs only when explicitly selected).
- **Validity constraint (pre-registered, non-negotiable):** the mock sees only OBSERVABLE state a
  real hop would reveal (fixture files, env, category, ambiguity notes) — it must NOT see the hidden
  test oracle (`checks`/`per_test`/expected outputs). Otherwise a "pass lift" is oracle leakage, not
  reachable information. codex-worker must pass the mock only spec+fixture, never the check list.
- Scope: the **agentic bank (n≈14)** only — micro tasks have no environment/observable state, so the
  mock has nothing legitimate to add there (and reach never fires on them).
- Framing note: this is the UPPER-BOUND version (resolve any strict-unanswerable observable question,
  not only lens-tagged reach questions) because no per-question lens tag currently reaches
  `outcome_eval`. If the upper bound clears, the fair reach-only version (needs lens-tag plumbing) is
  the iteration-four follow-up; if the upper bound fails, reach-only cannot beat it → candidate 3 parks.

## Smoke test
- Command/procedure: run the new arm on ONE agentic task (e.g. `config-or-env` / `env-policy`), show
  that ≥1 previously-unanswerable question is now resolved from the fixture with a concrete observed
  answer, and that the `nbq`/`baseline` arms on the same task are byte-identical to a run without the
  arm selected.
- Pass condition: ≥1 resolution happens; no oracle text appears in any mock answer; default arms
  unchanged.
- Stop condition: the mock answer quotes an expected test output / check ⇒ STOP (oracle leakage).

## Targeted tests
- Tests (offline, in `tests/run.py`): (1) `mock_investigator` resolves a fixture-observable question
  on a fixture-bearing fixture-row and REFUSES an unobservable one; (2) the mock is never handed the
  oracle (assert the prompt/context contains no `checks`/expected-output fields — a leakage guard);
  (3) inert-by-default pin — selecting no arm / the `nbq` arm yields identical qa/scores and makes
  zero mock calls; (4) arm plumbing (arm name accepted; unknown arm still raises).
- Inert-by-default pin: REQUIRED.
- Required assertions: leakage guard; default byte-identity; agentic-only guard (mock no-ops when a
  task has no fixture).

## Gate (arms, n, primary metric)
- Control arm: `nbq`.
- Experimental arm: `nbq-reach-investigate`.
- Also run: `baseline` (paired denominator, existing).
- Paired sample and n: agentic bank, n≈14; K=3; all-deepseek; `--strict-preflight`; cost columns on.
- Primary metric: paired Δpass (`nbq-reach-investigate` − `nbq`).
- Secondary diagnostics:
  1. unanswerable-rate drop vs `nbq` (sanity the mock resolved things);
  2. **the #30-unblock:** re-run `evals/probe_answerability.py` on the new arm's rows — does the
     answerability↔pass association now clear its SE with non-degenerate marginals?
  3. no oracle leakage (manual spot-check of resolved answers vs the hidden checks).

## Mechanical ADOPT rule (frozen before build)
- **Adopt the arm as a standing corpus-builder instrument** (like #33's opt-in instrument — NOT an
  nbq default flip; the formula/lenses are untouched) exactly when: Δpass > 0 with **wins ≥ 2× losses**
  (broad-win guard) AND unanswerable materially down AND zero oracle leakage.
- **#30 UN-PARKS** only if secondary (2) shows a non-degenerate answerability↔pass signal clearing SE
  on the new arm (then iteration four pre-registers the shipped reach-investigate mechanism + the
  non-self-rated answerability weight, re-gated on the objective harness).
- **Otherwise (Δpass ≤ 0, not broad, or leakage):** candidate 3's "resolving reach questions helps"
  thesis is NOT supported → park candidate 3; candidate 2 (nbq→relentless integration) rises as the
  corpus route. Leakage ⇒ discard the run and rebuild the mock.

## Efficiency budget (per-axis ceilings — verdict-rubric.md)
- Δcalls: +1 mock call per strict-unanswerable question (≤ K per task).
- Δtokens / Δwall: bounded by those extra calls.
- Ceilings (informational for the eval ARM; BINDING when/if a real reach-investigate integration
  ships): wall ≤ 10%, tokens ≤ 15% of an nbq run, calls ≤ +K. A shipped integration that busts any
  axis is adopt-with-knob-off or no-adopt. Report all three axes per arm regardless.

## Rollback (selector + flag)
- Selector: the arm name `nbq-reach-investigate` (absent ⇒ never runs; default arm set unchanged).
- Rollback: don't select the arm / remove it from the arm list.
- Absent behavior: existing arms + default run byte-identical.

## Journal stubs for BOTH outcomes
> **[candidate 3] — ADOPTED (corpus instrument) / #30 UN-PARKED.** Δpass … at n=14; unanswerable
> …→…; probe on new arm r=… (clears SE, non-degenerate). No leakage. Iteration four ships the
> reach-investigate weight. Δcost: wall …, tokens …, calls …. Evidence: …

> **[candidate 3] — NO ADOPT.** Δpass … at n=14 (not a broad win / ≤0). Resolving observable
> questions did not lift objective pass, so the reach→investigate thesis is unsupported on this bank.
> #30 stays parked; candidate 2 (nbq→relentless) becomes the corpus route. Re-open only if …
