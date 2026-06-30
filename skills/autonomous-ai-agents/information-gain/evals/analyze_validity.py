#!/usr/bin/env python3
"""analyze_validity.py — de-confounded per-regime analysis of a validate_evsi run (#21).

Requires rows with BOTH realized_change and realized_stakes (i.e. produced after the stakes
judge was added). It answers what P1a could not:

  1. Stakes-judge calibration — does PROJECTED stakes predict REALIZED stakes? (the missing half)
  2. Independence — are realized_change and realized_stakes actually distinct signals, or did the
     judge conflate them? (if conflated, regret adds nothing)
  3. De-confounded formula test — does projected value / EVSI / max-Δ / U predict realized REGRET
     (= realized_change × realized_stakes, both MEASURED — no projected-stakes reuse)? Per regime.

Regimes are inferred from `cat` (testbank category): ask-user / go-find-out / just-do-it.

Usage:  python3 evals/analyze_validity.py /Users/dadleet/.hermes/evsi_validity.json
"""

import json
import sys
from collections import defaultdict

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from analyze_evsi import pearson, spearman  # noqa: E402

# category -> regime (ask = genuine user forks; find = derivable/research; do = low-value default)
REGIME = {
    "life": "ask-user", "planning": "ask-user", "finance": "ask-user", "code-review": "ask-user",
    "code-feature": "ask-user", "code-debug": "ask-user", "devops": "ask-user", "docs": "ask-user",
    "web-research": "go-find-out", "knowledge": "go-find-out", "calendar": "go-find-out",
    "comms-retrieve": "go-find-out",
    "email": "just-do-it", "comms-send": "just-do-it", "data": "just-do-it",
    "system-files": "just-do-it", "automation": "just-do-it",
}


def load(path):
    rows = json.load(open(path)).get("rows", [])
    return [r for r in rows if r.get("realized_change") is not None
            and r.get("realized_stakes") is not None]


def by_question(rows):
    g = defaultdict(list)
    for r in rows:
        g[(r["prompt"], r["question"])].append(r)
    out = []
    for (prompt, q), rs in g.items():
        ptot = sum(max(0.0, x["prob"]) for x in rs) or 1.0
        for x in rs:
            x["_pn"] = max(0.0, x["prob"]) / ptot
        out.append({
            "prompt": prompt, "cat": rs[0].get("cat"), "regime": REGIME.get(rs[0].get("cat"), "?"),
            "rc": sum(x["_pn"] * x["realized_change"] for x in rs),
            "regret": sum(x["_pn"] * x["realized_regret"] for x in rs),
            "max_delta": max(x["projected_delta"] for x in rs),
            "q_u": rs[0]["q_u"], "q_evsi": rs[0]["q_evsi"], "q_value": rs[0]["q_value"],
        })
    return out


def _s(a, b):
    v = spearman(a, b)
    return f"{v:+.2f}" if v is not None else " n/a"


def main(argv):
    path = argv[1] if len(argv) > 1 else "/Users/dadleet/.hermes/evsi_validity.json"
    rows = load(path)
    qs = by_question(rows)
    for r in rows:
        r["regime"] = REGIME.get(r.get("cat"), "?")
    print(f"\nloaded {len(rows)} answer-rows / {len(qs)} questions / "
          f"{len({q['prompt'] for q in qs})} prompts\n")

    ps = [r["stakes"] for r in rows]
    rs_ = [r["realized_stakes"] for r in rows]
    rc = [r["realized_change"] for r in rows]
    print("=" * 68)
    print("1. STAKES-JUDGE CALIBRATION  (projected stakes vs realized stakes)")
    print("=" * 68)
    print(f"   per-answer Pearson={pearson(ps,rs_):+.2f}  Spearman={_s(ps,rs_)}   (n={len(rows)})")
    print("   per regime:")
    by_reg = defaultdict(list)
    for r in rows:
        by_reg[r["regime"]].append(r)
    for reg, rr in sorted(by_reg.items()):
        a = [x["stakes"] for x in rr]; b = [x["realized_stakes"] for x in rr]
        print(f"     {reg:<12} n={len(rr):>2}  Spearman(proj_stakes, real_stakes)={_s(a,b)}")

    print("\n" + "=" * 68)
    print("2. INDEPENDENCE  (are realized_change and realized_stakes distinct?)")
    print("=" * 68)
    print(f"   Spearman(realized_change, realized_stakes) = {_s(rc, rs_)}  "
          f"(near 1.0 => judge conflated them; regret adds nothing)")

    print("\n" + "=" * 68)
    print("3. DE-CONFOUNDED FORMULA TEST  (projected vs realized REGRET = Δ×stakes, both measured)")
    print("=" * 68)
    forms = [("value √(U·EVSI)", "q_value"), ("EVSI-only", "q_evsi"),
             ("max-Δ", "max_delta"), ("U-only", "q_u")]
    print(f"   {'target = realized_regret (de-confounded)':<42}{'realized_change (clean Δ)':<22}")
    for name, key in forms:
        rho_reg = spearman([q[key] for q in qs], [q["regret"] for q in qs])
        rho_rc = spearman([q[key] for q in qs], [q["rc"] for q in qs])
        print(f"   {name:<20} regret ρ={(f'{rho_reg:+.2f}' if rho_reg is not None else 'n/a'):>6}"
              f"      change ρ={(f'{rho_rc:+.2f}' if rho_rc is not None else 'n/a'):>6}")

    print("\n   per regime (value √(U·EVSI) vs realized_regret):")
    by_regq = defaultdict(list)
    for q in qs:
        by_regq[q["regime"]].append(q)
    for reg, qq in sorted(by_regq.items()):
        mv = sum(q["q_value"] for q in qq) / len(qq)
        mr = sum(q["regret"] for q in qq) / len(qq)
        print(f"     {reg:<12} n={len(qq):>2}  mean value={mv:.2f}  mean regret={mr:.2f}  "
              f"within ρ={_s([q['q_value'] for q in qq], [q['regret'] for q in qq])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
