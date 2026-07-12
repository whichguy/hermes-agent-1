#!/usr/bin/env python3
"""Offline analysis for outcome_eval.py's opt-in paired answer-vs-assume ablation."""

import argparse
import json
import math
import os
import statistics
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))
sys.path.insert(0, _HERE)

from analyze_evsi import spearman  # noqa: E402
from outcome_eval import _sign_test_p  # noqa: E402


CALIBRATION_CAVEAT = ("This per-task correlation partly recovers nbq's own calibration rather "
                      "than being an independent signal; the low-EVSI control carries the "
                      "attribution test.")


def _by_arm(rows):
    by = {}
    for row in rows:
        if "error" not in row:
            by.setdefault(row.get("arm"), {})[row.get("task")] = row
    return by


def _contrast(by, left, right, task_ids=None):
    shared = sorted(set(by.get(left, {})) & set(by.get(right, {})))
    if task_ids is not None:
        wanted = set(task_ids)
        shared = [task for task in shared if task in wanted]
    deltas = [by[left][task]["frac"] - by[right][task]["frac"] for task in shared]
    wins = sum(1 for delta in deltas if delta > 0)
    losses = sum(1 for delta in deltas if delta < 0)
    n = len(deltas)
    mean = statistics.mean(deltas) if deltas else None
    se = statistics.pstdev(deltas) / math.sqrt(n) if n >= 2 else None
    return {
        "n": n, "tasks": shared, "deltas": deltas, "mean": mean,
        "wins": wins, "losses": losses, "ties": n - wins - losses,
        "se": se, "mean_clears_se": bool(mean is not None and se is not None and abs(mean) > se),
        "sign_p": _sign_test_p(wins, losses),
        "broad_win": wins >= 2 * losses and wins > losses,
    }


def _secondary(by, left, right):
    contrast = _contrast(by, left, right)
    return {key: contrast[key] for key in ("n", "tasks", "mean", "wins", "losses")}


def _correlation(by, metric):
    answer, assume = by.get("answer", {}), by.get("assume", {})
    xs, ys, tasks = [], [], []
    for task in sorted(set(answer) & set(assume)):
        meta = answer[task].get("meta") or {}
        if metric == "values":
            values = meta["injected_values"] if "injected_values" in meta else meta.get("q_values")
        else:
            values = meta.get("injected_evsis")
        if not isinstance(values, list) or not values:
            continue
        xs.append(statistics.mean(values))
        ys.append(answer[task]["frac"] - assume[task]["frac"])
        tasks.append(task)
    if len(xs) < 4:
        return {"n": len(xs), "tasks": tasks, "rho": None, "status": "insufficient n",
                "caveat": CALIBRATION_CAVEAT}
    return {"n": len(xs), "tasks": tasks, "rho": spearman(xs, ys), "status": "ok",
            "caveat": CALIBRATION_CAVEAT}


def _paired_design(by):
    assume, answer = by.get("assume", {}), by.get("answer", {})
    offending = []
    for task in sorted(set(assume) & set(answer)):
        assume_row, answer_row = assume[task], answer[task]
        if (assume_row.get("questions") != answer_row.get("questions")
                or assume_row.get("questions") != assume_row.get("shared_topk")
                or answer_row.get("questions") != answer_row.get("shared_topk")):
            offending.append(task)
    return {"valid": not offending, "offending_task_ids": offending}


def _passes_primary(stats):
    return bool(stats["mean"] is not None and stats["mean"] > 0
                and stats["broad_win"] and stats["mean_clears_se"])


def assemble_stats(data):
    by = _by_arm(data.get("rows", []))
    answer_assume = _contrast(by, "answer", "assume")
    answer_lowevsi = _contrast(by, "answer", "answer-lowevsi")
    clean_tasks = [task for task, row in by.get("answer", {}).items()
                   if row.get("revealed", 0) >= 1]
    clean = {
        "answer_minus_assume": _contrast(by, "answer", "assume", clean_tasks),
        "answer_minus_answer_lowevsi": _contrast(by, "answer", "answer-lowevsi", clean_tasks),
    }
    passes_assume, passes_lowevsi = _passes_primary(answer_assume), _passes_primary(answer_lowevsi)
    if passes_assume and passes_lowevsi:
        verdict = "PROCEED"
    elif passes_assume and not passes_lowevsi:
        verdict = "ATTRIBUTION_FAIL"
    else:
        verdict = "NULL"
    return {
        "paired_design": _paired_design(by),
        "primary": {"answer_minus_assume": answer_assume,
                    "answer_minus_answer_lowevsi": answer_lowevsi},
        "secondary": {"answer_minus_baseline": _secondary(by, "answer", "baseline"),
                      "assume_minus_baseline": _secondary(by, "assume", "baseline")},
        "clean_contrast_subset": {"n_answer_revealed": len(clean_tasks), "tasks": sorted(clean_tasks),
                                  "primary": clean},
        "per_task_correlations": {
            "mean_topk_value_vs_answer_minus_assume": _correlation(by, "values"),
            "mean_topk_evsi_vs_answer_minus_assume": _correlation(by, "evsis"),
        },
        "verdict": verdict,
    }


def _fmt_primary(label, stats):
    mean = "n/a" if stats["mean"] is None else f"{stats['mean']:+.4f}"
    se = "n/a" if stats["se"] is None else f"{stats['se']:.4f}"
    return (f"{label}: n={stats['n']} mean={mean} wins/losses/ties="
            f"{stats['wins']}/{stats['losses']}/{stats['ties']} SE={se} "
            f"clears_SE={stats['mean_clears_se']} broad_win={stats['broad_win']} "
            f"sign_p={stats['sign_p']:.4f}")


def _fmt_usage(usage):
    return (f"calls={usage.get('calls', 0)} input_tokens={usage.get('input_tokens', 0)} "
            f"output_tokens={usage.get('output_tokens', 0)} "
            f"model_seconds={usage.get('model_seconds', 0)}")


def _cost_lines(data):
    rows = data.get("rows", [])
    schema = data.get("ablation_schema")
    by = _by_arm(rows)
    lines = ["COSTS"]
    if schema == 2:
        generation_usage = data.get("generation_usage") or {}
        shared = {field: sum(usage.get(field, 0) for usage in generation_usage.values())
                  for field in ("calls", "input_tokens", "output_tokens", "model_seconds")}
        lines.append("shared once-per-task generation (sum): " + _fmt_usage(shared))
        label = "MARGINAL mean per arm"
    else:
        label = "shared (legacy schema) mean per arm"
    for arm in sorted(by):
        usages = [row.get("meta", {}).get("usage") for row in by[arm].values()]
        usages = [usage for usage in usages if isinstance(usage, dict)]
        if not usages:
            continue
        mean = {field: sum(usage.get(field, 0) for usage in usages) / len(usages)
                for field in ("calls", "input_tokens", "output_tokens", "model_seconds")}
        lines.append(f"{label} {arm}: " + _fmt_usage(mean))
    return lines


def format_stats(stats, data=None):
    primary = stats["primary"]
    lines = ["ANSWER-VS-ASSUME PAIRED ABLATION", _fmt_primary(
        "Δpass(answer − assume)", primary["answer_minus_assume"]), _fmt_primary(
        "Δpass(answer − answer-lowevsi)", primary["answer_minus_answer_lowevsi"])]
    for label, result in stats["secondary"].items():
        mean = "n/a" if result["mean"] is None else f"{result['mean']:+.4f}"
        lines.append(f"secondary {label}: n={result['n']} mean={mean} "
                     f"wins/losses={result['wins']}/{result['losses']}")
    clean = stats["clean_contrast_subset"]
    lines.append(f"clean-contrast subset (answer revealed >=1): n={clean['n_answer_revealed']}")
    lines.append(_fmt_primary("  Δpass(answer − assume)", clean["primary"]["answer_minus_assume"]))
    lines.append(_fmt_primary("  Δpass(answer − answer-lowevsi)",
                              clean["primary"]["answer_minus_answer_lowevsi"]))
    for label, corr in stats["per_task_correlations"].items():
        rho = "insufficient n" if corr["status"] == "insufficient n" else str(corr["rho"])
        lines.append(f"per-TASK correlation ({label}): n={corr['n']} rho={rho}")
        lines.append(f"  caveat: {corr['caveat']}")
    validity = stats["paired_design"]
    lines.append("PAIRED-DESIGN: VALID" if validity["valid"] else
                 f"PAIRED-DESIGN: INVALID offending task ids: {validity['offending_task_ids']}")
    if data is not None:
        lines.extend(_cost_lines(data))
    lines.append(f"VERDICT: {stats['verdict']}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--json", dest="json_out")
    args = parser.parse_args(argv)
    try:
        with open(args.input, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        stats = assemble_stats(data)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(stats, fh, indent=2, sort_keys=True)
                fh.write("\n")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(format_stats(stats, data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
