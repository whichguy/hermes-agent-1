#!/usr/bin/env python3
"""analyze_evsi.py — post-hoc analysis of validate_evsi.py output (P1a + P1c). No model calls.

Reads the rows JSON (one row per question×answer with projected vs realized change) and reports:

  P1a calibration
    - per-answer correlation: does projected `delta_plan` predict `realized_change`?
      (Pearson + Spearman + a binned calibration curve + saturation diagnostics)
    - per-question: does projected EVSI / value track the question's REALIZED value
      (Σ_a P'(a)·realized_change(a), P' = prob renormalized over tested answers)?

  P1c formula ablations (the near-free study)
    - rank questions per prompt under alternative projected formulas:
        value=√(U·EVSI)  |  EVSI-only  |  U-only  |  max-Δ  |  mean-Δ (P-weighted)
      and measure which projected ranking best matches the REALIZED ranking
      (mean Spearman across prompts). The winner is the formula worth shipping.

Usage:  python3 evals/analyze_evsi.py /Users/dadleet/.hermes/evsi_validation.json
"""

import json
import math
import sys
from collections import defaultdict


# ---- pure-python stats (no scipy) -------------------------------------------

def _ranks(xs):
    """Average-rank (handles ties) for Spearman."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def spearman(xs, ys):
    if len(xs) < 2:
        return None
    return pearson(_ranks(xs), _ranks(ys))


# ---- load + group ------------------------------------------------------------

def load_rows(path):
    with open(path) as f:
        data = json.load(f)
    return [r for r in data.get("rows", []) if "realized_change" in r and r["realized_change"] is not None]


def by_question(rows):
    """(prompt, question) -> dict with tested answers + projected question scores."""
    groups = defaultdict(list)
    for r in rows:
        groups[(r["prompt"], r["question"])].append(r)
    out = []
    for (prompt, q), rs in groups.items():
        ptot = sum(max(0.0, x["prob"]) for x in rs) or 1.0
        for x in rs:
            x["_pn"] = max(0.0, x["prob"]) / ptot  # renormalized over tested answers
        realized_change = sum(x["_pn"] * x["realized_change"] for x in rs)
        realized_evsi = sum(x["_pn"] * x["realized_change"] * x["stakes"] for x in rs)
        max_delta = max(x["projected_delta"] for x in rs)
        mean_delta = sum(x["_pn"] * x["projected_delta"] for x in rs)
        out.append({
            "prompt": prompt, "question": q, "n_ans": len(rs),
            "q_u": rs[0]["q_u"], "q_evsi": rs[0]["q_evsi"], "q_value": rs[0]["q_value"],
            "max_delta": max_delta, "mean_delta": mean_delta,
            "realized_change": realized_change, "realized_evsi": realized_evsi,
        })
    return out


# ---- P1a ---------------------------------------------------------------------

def p1a(rows, questions):
    pd = [r["projected_delta"] for r in rows]
    rc = [r["realized_change"] for r in rows]
    print("=" * 70)
    print("P1a — CALIBRATION: does projection predict realized change?")
    print("=" * 70)
    print(f"\nper-answer (n={len(rows)}):  projected_delta vs realized_change")
    print(f"  Pearson  r = {pearson(pd, rc):+.3f}" if pearson(pd, rc) is not None else "  Pearson  n/a")
    print(f"  Spearman ρ = {spearman(pd, rc):+.3f}" if spearman(pd, rc) is not None else "  Spearman n/a")

    # binned calibration curve
    print("\n  calibration curve (mean realized | projected_delta bin):")
    bins = [(0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01)]
    for lo, hi in bins:
        sel = [r["realized_change"] for r in rows if lo <= r["projected_delta"] < hi]
        bar = "█" * round((sum(sel) / len(sel)) * 20) if sel else ""
        print(f"    Δ[{lo:.1f},{hi:.1f}): n={len(sel):>2}  mean_realized={sum(sel)/len(sel):.2f} {bar}"
              if sel else f"    Δ[{lo:.1f},{hi:.1f}): n= 0")

    # saturation diagnostics (the realized judge clustering at 0/1)
    at1 = sum(1 for x in rc if x >= 0.99)
    at0 = sum(1 for x in rc if x <= 0.01)
    print(f"\n  realized saturation: {at0}/{len(rc)} at 0.0, {at1}/{len(rc)} at 1.0 "
          f"({100*(at0+at1)/len(rc):.0f}% extreme) — discrimination concern if high")

    # per-question: projected EVSI / value vs realized
    qe = [q["q_evsi"] for q in questions]
    qv = [q["q_value"] for q in questions]
    qr = [q["realized_change"] for q in questions]
    qre = [q["realized_evsi"] for q in questions]
    print(f"\nper-question (n={len(questions)}):")
    print(f"  projected EVSI  vs realized_change : Spearman ρ = {spearman(qe, qr)}")
    print(f"  projected value vs realized_change : Spearman ρ = {spearman(qv, qr)}")
    print(f"  projected EVSI  vs realized_EVSI   : Spearman ρ = {spearman(qe, qre)}")


# ---- P1c ---------------------------------------------------------------------

FORMULAS = {
    "value √(U·EVSI)": lambda q: q["q_value"],
    "EVSI-only":       lambda q: q["q_evsi"],
    "U-only":          lambda q: q["q_u"],
    "max-Δ":           lambda q: q["max_delta"],
    "mean-Δ (Pwt)":    lambda q: q["mean_delta"],
}


def p1c(questions, target_key="realized_change"):
    print("\n" + "=" * 70)
    print(f"P1c — FORMULA ABLATIONS: which projected formula best matches realized")
    print(f"      (target = {target_key}; mean Spearman of per-prompt rankings)")
    print("=" * 70)
    by_prompt = defaultdict(list)
    for q in questions:
        by_prompt[q["prompt"]].append(q)
    results = {}
    for name, fn in FORMULAS.items():
        rhos = []
        for prompt, qs in by_prompt.items():
            if len(qs) < 2:
                continue
            rho = spearman([fn(q) for q in qs], [q[target_key] for q in qs])
            if rho is not None:
                rhos.append(rho)
        results[name] = sum(rhos) / len(rhos) if rhos else None
    for name, mean_rho in sorted(results.items(), key=lambda kv: (kv[1] is None, -(kv[1] or -9))):
        star = "  <- best" if mean_rho is not None and mean_rho == max(
            (v for v in results.values() if v is not None), default=None) else ""
        print(f"  {name:<18} mean ρ = {mean_rho:+.3f}{star}" if mean_rho is not None
              else f"  {name:<18} mean ρ = n/a")
    return results


def main(argv):
    path = argv[1] if len(argv) > 1 else "/Users/dadleet/.hermes/evsi_validation.json"
    rows = load_rows(path)
    if not rows:
        print(f"no usable rows in {path}", file=sys.stderr)
        return 1
    questions = by_question(rows)
    print(f"\nloaded {len(rows)} answer-rows / {len(questions)} questions / "
          f"{len({q['prompt'] for q in questions})} prompts from {path}\n")
    p1a(rows, questions)
    p1c(questions, "realized_change")
    p1c(questions, "realized_evsi")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
