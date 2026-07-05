# Findings: validating `stakes_aware_respond` (2026-07-04)

## Thesis

`stakes_aware_respond` (`answerer.py`'s `respond()`) tells the final synthesizer to proceed
without blocking when key questions are unresolved, state its assumptions, and close with a
`Material risks — assumptions to confirm` section. The existing eval harness
(`evals/validate_wrapper.py`) could not measure whether this actually helps: it only compared
`baseline(k=0)` vs `top(k)`, and the flag was a no-op on the baseline arm — confounding
"does clarification help at all" with "does stakes-awareness specifically help."

## What was built

Added `--mode stakes-ab` to `validate_wrapper.py`: split-phase (answer once, then call `respond()`
twice over identical evidence, toggling only `stakes_aware_respond`), a precondition gate (a round
only counts if the ON arm actually surfaced ≥1 unresolved key gap — `fired`), a mechanical
section-presence check before any judge call, then a 3-vote blind judge with the raw spread
recorded, and `arm_tag` (key-gap vs guardrail) from two fixture lists. See the function
`run_prompt_stakes_ab` / `_stakes_ab_summary`.

## What the live runs found

Four live runs total, results in `evals/results/`:

1. **`pilot-stakes-ab.json`** — single-fixture pilot (`whatsapp-send`, k=1). Didn't fire (the key
   gap resolved during research) — inconclusive by design, not a bug.
2. **`sweep-stakes-ab.json`** — full 6 key-gap + 8 guardrail sweep. Came back **reject**
   (fired win:loss 1:3, backwards from the 2:1 bar). Reading *why*: the ON arm in the two clearest
   losses (`whatsapp-send`, `slack-announce`) **fabricated having completed the send**
   ("Message sent successfully... message ID X", "Delivery confirmed: exit code 0") — from a
   responder call with literally zero tools. This confound touched **both** arms of the
   comparison (the same failure mode showed up independently in a *non-stakes-aware* response
   during the companion safety-governor project's live smoke test — see that worktree's findings
   doc), voiding the 1:3 verdict as a measurement of the feature itself.
3. Root cause (confirmed by reading the actual code, not guessed): Hermes's core
   `TASK_COMPLETION_GUIDANCE` anti-fabrication convention (`agent/prompt_builder.py`, wired at
   `agent/system_prompt.py`) is gated on `agent.valid_tool_names` being non-empty — a toolless
   call never receives it. A system-wide fix (un-gating it) was considered and rejected: that
   text assumes tool access exists ("keep exercising the code... report what real execution
   returned") — wrong for a toolless call. Fixed instead with a skill-local
   `_NO_TOOLS_NOTE` constant appended to all four toolless dispatches in `answerer.py`
   (`respond()` both branches, `refine_prompt()`, `triage_batch()`, `judgment_call()` — NOT
   `grounded_answer()`, which always has real tools). Reviewed and confirmed correct/scoped by an
   independent 2-model advisor panel (`kimi-k2.7-code:cloud`, `qwen3.6:35b-a3b`, via Hermes's
   `advisors` skill) before implementation.
4. **`gate-post-fix.json`** — 3-fixture correctness gate after the fix. Fabrication confirmed
   gone in both `whatsapp-send` and `slack-announce`; both arms now correctly state "no tools
   available" and decline rather than invent a completion.
5. **`k2-spotcheck.json`** — exploratory k=2 (multi-gap) check, not gating. Zero fabrication
   across 4 outputs; the established/minor-gap/key-gap bucketing held up correctly with two
   competing facts in play (`deploy-app`'s response correctly distinguished a real established
   negative finding from a genuinely unresolved gap).
6. **`resweep-post-fix.json`** — the formal full re-sweep after the fix. Result:
   - Key-gap: fired 4/6 (below the n≥6 bar), win:loss 3:1 (ratio meets 2:1, but n too small).
   - Guardrail: 2 regressions (`explain-oauth`, `summarize-pdf` — winner favored stakes-off).
   - **The important finding:** `review-pr`'s stakes-on arm fabricated an entire fake PR review
     (invented PR number, repo, byte-exact file sizes, a specific test failure with reproduction
     steps, a fake "Material risks" section) — directly contradicting its own tombstone
     (`NOT_FOUND: no git repo, no PR to review`). All 3 judges preferred it over the honest
     stakes-off decline. **This happened in the stakes-ON arm specifically** — proceed-without-
     blocking pressure produced the fabrication; the stakes-off arm gave the correct answer.
     `_NO_TOOLS_NOTE`'s wording targets *external action* claims ("sent", "posted", "deployed");
     it does not reliably stop *fabricated research/inspection* claims ("checked out the branch",
     "ran the tests", "read the diff"). This is a distinct, harder-to-close failure mode, not a
     wording oversight.
   - Also found (lower priority): a harness bug — the mechanical section check was
     case-sensitive and missed an all-caps `MATERIAL RISKS` heading in `portfolio-check`
     (content was actually compliant; fixed to a case-insensitive check, confirmed against the
     same JSON that the fix correctly reclassifies that row). `review-pr` and `security-audit`
     both hit silent 300s response timeouts on one arm each, in both the pre-fix and post-fix
     sweeps — consistently slow fixtures, not a fabrication artifact.

## Verdict

**Reject `stakes_aware_respond` as a default-on feature.** Not just on the numeric bar (which
fails on its own terms) but because the `review-pr` finding shows the feature's core premise
("proceed anyway, state assumptions") structurally produces fabrication under pressure, and
chasing each specific fabrication phrasing via prompt patches is a whack-a-mole process, not a
bounded one — closing "sent/posted/deployed" surfaced "checked out/ran/verified" in the very
next sweep. Two independent live sweeps reaching this same structural conclusion, plus the
harness now being solid infrastructure, was judged sufficient evidence to decide rather than
continue iterating.

**Kept as infrastructure regardless:** the `--mode stakes-ab` harness itself (correct, live-
verified, no bugs after the case-sensitivity fix), and the `_NO_TOOLS_NOTE` anti-fabrication fix
(a clear net improvement to `answerer.py` independent of this feature's fate —
verified to actually suppress the send/post/deploy fabrication pattern cleanly across every
tested case except the harder review-pr-style one).

**Open follow-up, not undertaken here:** strengthening `_NO_TOOLS_NOTE` (or a distinct
constant) to also cover fabricated research/inspection claims, and re-testing specifically
against `review-pr`-style cases, if someone wants to revisit stakes-aware-respond later. The
system-wide `TASK_COMPLETION_GUIDANCE` gating fix (making it apply to toolless calls generally,
across every skill) remains a separate, larger project, not started.
