# Benchmark findings (2026-06, first pass)

A first-pass benchmark + ablation of the information-gain skill, with adversarial verification of the
conclusions. **Directional, not settled** — 16 cells, one prompt set, focus configs n=1, breadth n=2.

## Setup

- **Harness:** `evals/benchmark.py` (in-process matrix runner; captures `result['usage']` =
  calls/tokens/wall and adjudicates each run via `evals/adjudicator.py`). Run on the host against
  `localhost:11434` (immune to `hermes` container restarts) with incremental writes.
- **Prompts (all underspecified):** `usaw-calendar` (build a USAW→calendar sync), `buy-rent`
  ("Should I buy or rent a home?"), `gtm-plan` (B2B SaaS go-to-market), `remote-hybrid`
  (remote vs hybrid trade-offs for a 200-person co).
- **Configs:** `focus-fast` (fast/fast/fast, deterministic, 1 rep), `focus-default`
  (glm/fast/deepseek, 1 rep), `breadth-fast` (fast/fast/fast, sampled, 2 reps).
- **Adjudicator:** `deepseek` scoring framing/relevance/value/diversity/calibration (0–1); a run is
  `acceptable` iff framing+relevance+calibration ≥ 0.6.

## Raw results (16 cells)

| prompt | config | rep | bucket | n_pre | top_v | tok | wall | mean_ans | reord | acc | calib | qrel |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|:--:|---:|---:|
| usaw-calendar | focus-fast | 0 | 1 | 1 | .69 | 8158 | 28 | .95 | 0 | n | .10 | .20 |
| usaw-calendar | focus-default | 0 | 1 | 0 | .45 | 8316 | 29 | .95 | 0 | n | .10 | .20 |
| usaw-calendar | breadth-fast | 0 | 18 | 2 | .62 | 58307 | 222 | .95 | 0 | n | .30 | .30 |
| usaw-calendar | breadth-fast | 1 | 18 | 3 | .65 | 48779 | 184 | .95 | 0 | n | .30 | .55 |
| buy-rent | focus-fast | 0 | 6 | 5 | .78 | 8148 | 29 | .95 | 0 | Y | .85 | .85 |
| buy-rent | focus-default | 0 | 4 | 3 | .67 | 8014 | 28 | .94 | 3 | Y | .90 | .85 |
| buy-rent | breadth-fast | 0 | 15 | 13 | .78 | 28803 | 116 | .95 | 0 | Y | .80 | .85 |
| buy-rent | breadth-fast | 1 | 18 | 18 | .74 | 38799 | 157 | .95 | 0 | Y | .90 | .85 |
| gtm-plan | focus-fast | 0 | 5 | 5 | .75 | 8395 | 32 | .95 | 0 | Y | .80 | .80 |
| gtm-plan | focus-default | 0 | 3 | 1 | .63 | 8309 | 44 | .95 | 0 | n | .30 | .40 |
| gtm-plan | breadth-fast | 0 | 18 | 18 | .88 | 42755 | 144 | .95 | 0 | n | .30 | .40 |
| gtm-plan | breadth-fast | 1 | 18 | 18 | .77 | 49798 | 192 | .95 | 0 | Y | .80 | .75 |
| remote-hybrid | focus-fast | 0 | 4 | 2 | .71 | 8205 | 30 | .95 | 0 | Y | .70 | .70 |
| remote-hybrid | focus-default | 0 | 4 | 0 | .58 | 8367 | 46 | .95 | 0 | Y | .60 | .70 |
| remote-hybrid | breadth-fast | 0 | 18 | 9 | .74 | 36244 | 127 | .95 | 0 | Y | .80 | .70 |
| remote-hybrid | breadth-fast | 1 | 16 | 7 | .70 | 38373 | 140 | .95 | 0 | n | .40 | .70 |

Overall acceptable rate **9/16 = 56%** · buy-rent 4/4 · remote-hybrid 3/4 · gtm-plan 2/4 · usaw 0/4.

## Verified findings (survived adversarial recomputation)

1. **Cost — breadth is the whole bill.** breadth-fast ≈ 5.2× tokens / 4.4× calls / 4.4–5.4× wall vs
   focus, and **84% of all tokens** in the grid. Per-question: focus-fast cheapest (2057 tok/q),
   focus-default priciest (2750 tok/q), breadth 2459 tok/q (undercuts focus-default by yielding more,
   but coverage is hard-capped at ~18 — 6 of 8 breadth cells hit exactly 18).
2. **Breadth buys coverage, not reliable quality.** It beat focus-fast on `top_value` on only
   gtm-plan; and its verdict **flips run-to-run** (gtm false→true, remote true→false) — not warmup.
3. **Answerability is inert.** Pinned at 0.95 in 15/16 cells; reordered the ranking in exactly 1
   (a fully-confounded cell). → **removed from the algorithm** (this finding drove that change).
4. **usaw-calendar fails 0/4** — framing stays high (0.8–0.9) while relevance/calibration/value
   collapse → niche-domain / model-knowledge mismatch, not a pipeline bug.

## Critical caveats (what the verification killed)

- **The judge is perfectly confounded with config** (`fast` only on focus-fast/breadth, `deepseek`
  only on focus-default). The "deepseek judge is better-calibrated" claim is **NOT supported** here —
  the deltas are co-occurrences, not demonstrated judge properties. A de-confounded run is required.
- focus configs are **n=1**; breadth **n=2** with visible run-to-run swing. Config and per-prompt
  rankings rest on single-cell differences — treat as directional.
- `total_tok` measures the local pipeline only; the cloud judge's own tokens are not counted.

## Implication for the rating algorithm (the open question)

This benchmark put our **internal `value`** next to an **external quality judgment** for the first
time, and they **diverged** (e.g. usaw focus-fast: `value` 0.69 but adjudicated relevance 0.20). We
have never validated that `value` predicts *realized* improvement to the response. The EVSI
*structure* is sound; the suspect links are the **input estimates** (LLM Δ/stakes saturate),
the **absolute-threshold scale** (model-dependent), and **unvalidated validity**.

→ **Phase 1 ran this validation** — see `evsi-validation-findings.md`. Partial answer: the **Δ
component is directionally calibrated** (ρ=0.39, cluster p=0.005), but the **full stakes-weighted
EVSI is not-yet-validated** (null vs the clean realized-change signal; its only positive correlation
is a stakes-reuse confound), and **`U` is inert** (0/40 reorderings). The wrapper is gated on a
de-confounded re-run (`roadmap.md` Phase 1 / #21).
