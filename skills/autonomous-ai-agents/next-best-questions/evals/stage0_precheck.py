#!/usr/bin/env python3
"""Read-only Stage-0 structural pre-check for the paired nbq ablation."""

import argparse
import json
import os
import sys


DEFAULT_INPUTS = [
    os.path.expanduser("~/.hermes/outcome_eval_32.json"),
    os.path.expanduser("~/.hermes/outcome_eval_iter3.json"),
]
THRESHOLD_LINE = "≥ ⅓ of tasks have ≥1 revealed high-EVSI (top-K) question"


def _task_stats(row, k):
    qa = row.get("qa") or []
    n_revealed = sum(1 for entry in qa[:k] if entry.get("revealed") is True)
    q_values = (row.get("meta") or {}).get("q_values")
    return {
        "task": row.get("task"),
        "n_revealed_topk": n_revealed,
        "has_revealed": n_revealed >= 1,
        "q_values_present": q_values is not None,
        "q_values_len": len(q_values) if isinstance(q_values, list) else 0,
        "degenerate": not isinstance(q_values, list) or not q_values,
    }


def _aggregate(tasks):
    n_tasks = len(tasks)
    revealed = sum(1 for task in tasks if task["has_revealed"])
    return {
        "n_tasks": n_tasks,
        "tasks_with_revealed": revealed,
        "frac": revealed / n_tasks if n_tasks else 0.0,
        "degenerate_tasks": sum(1 for task in tasks if task["degenerate"]),
    }


def assemble_stats(inputs, k=3, threshold=0.3333):
    per_input = []
    combined_tasks = []
    for path in inputs:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        tasks = [_task_stats(row, k) for row in data.get("rows", [])
                 if row.get("arm") == "nbq" and "qa" in row]
        combined_tasks.extend(tasks)
        per_input.append({"input": path, "tasks": tasks, "aggregate": _aggregate(tasks)})
    combined = _aggregate(combined_tasks)
    return {
        "inputs": per_input,
        "combined": combined,
        "k": k,
        "threshold": threshold,
        "threshold_line": THRESHOLD_LINE,
        "verdict": "GO" if combined["frac"] >= threshold else "NO-GO",
        "modal_answer_caveat": ("modal_answer coverage is NOT present in these existing durable "
                                "JSONs; it is only checkable at run time, not from historical logs."),
    }


def format_stats(stats):
    lines = ["STAGE 0 — answerable high-EVSI contrast pre-check"]
    for source in stats["inputs"]:
        lines.extend(["", f"INPUT: {source['input']}",
                      "  task                         revealed/top-K  q_values  degenerate"])
        for task in source["tasks"]:
            q_values = ("present" if task["q_values_present"] else "missing")
            lines.append(f"  {str(task['task']):<28} {task['n_revealed_topk']:>3}/{stats['k']:<9} "
                         f"{q_values}:{task['q_values_len']:<3} {task['degenerate']}")
        aggregate = source["aggregate"]
        lines.append(f"  subtotal: {aggregate['tasks_with_revealed']}/{aggregate['n_tasks']} "
                     f"= {aggregate['frac']:.4f}; degenerate q_values={aggregate['degenerate_tasks']}")
    combined = stats["combined"]
    lines.extend(["", f"COMBINED: {combined['tasks_with_revealed']}/{combined['n_tasks']} "
                  f"= {combined['frac']:.4f}",
                  f"THRESHOLD: {stats['threshold_line']}",
                  f"VERDICT: {stats['verdict']}",
                  f"NOTE: {stats['modal_answer_caveat']}"])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="*", default=DEFAULT_INPUTS)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--threshold", type=float, default=0.3333)
    parser.add_argument("--json", dest="json_out")
    args = parser.parse_args(argv)
    try:
        stats = assemble_stats(args.inputs, args.k, args.threshold)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(stats, fh, indent=2, sort_keys=True)
                fh.write("\n")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(format_stats(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
