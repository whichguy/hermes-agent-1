#!/usr/bin/env python3
"""benchmark.py — multi-prompt × config × repetition benchmark of the skill.

For each cell it runs the skill in-process (capturing usage = calls/tokens/wall) and
adjudicates the result, then emits one JSON row per run. Aggregation/analysis is done
downstream. Focus configs are deterministic (temperature 0) so 1 rep suffices; breadth
samples at temperature>0 so it gets multiple reps to expose run-to-run variance.

Usage (inside the hermes container, where Ollama is reachable):
    python3 evals/benchmark.py --out /opt/data/infogain_bench.json
    python3 evals/benchmark.py --prompt-ids buy-rent --configs focus-fast   # smoke test
"""

import argparse
import json
import os
import statistics
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))
sys.path.insert(0, _HERE)

import infogain  # noqa: E402
import adjudicator  # noqa: E402

# Diverse prompts (all underspecified); wide bucket bounds so structural calibration
# doesn't fail — the LLM adjudicator's `calibration` criterion is the real signal.
PROMPTS = [
    {"id": "usaw-calendar", "expectation": "underspecified",
     "expect_min_bucket": 0, "expect_max_bucket": 20,
     "problem": "Build a service to sync USAW events into our team calendar."},
    {"id": "buy-rent", "expectation": "underspecified",
     "expect_min_bucket": 0, "expect_max_bucket": 20,
     "problem": "Should I buy or rent a home?"},
    {"id": "gtm-plan", "expectation": "underspecified",
     "expect_min_bucket": 0, "expect_max_bucket": 20,
     "problem": "Write a go-to-market plan for a new B2B SaaS product."},
    {"id": "remote-hybrid", "expectation": "underspecified",
     "expect_min_bucket": 0, "expect_max_bucket": 20,
     "problem": "Summarize the main trade-offs of remote vs hybrid work for a 200-person company."},
]

# config name -> overrides over DEFAULTS (+ mode preset). max_rounds=1 pins focus to one round.
CONFIGS = {
    "focus-fast": {"mode": "focus", "question_gen_model": "fast", "answer_model": "fast",
                   "value_judge_model": "fast", "max_rounds": 1},
    "focus-default": {"mode": "focus", "max_rounds": 1},  # glm / fast / deepseek
    "breadth-fast": {"mode": "breadth", "question_gen_model": "fast", "answer_model": "fast",
                     "value_judge_model": "fast"},
}
DEFAULT_REPS = {"focus-fast": 1, "focus-default": 1, "breadth-fast": 2}


def build_cfg(overrides):
    mode = overrides.get("mode", "focus")
    cfg = dict(infogain.DEFAULTS)
    cfg.update(infogain.MODES.get(mode, {}))
    cfg.update({k: v for k, v in overrides.items() if k != "mode"})
    cfg["mode"] = mode
    return cfg


def _crit(verdict, name):
    c = (verdict.get("judged") or {}).get("criteria") or {}
    return (c.get(name) or {}).get("score")


def run_cell(prompt, cname, rep, judge_model, judge_timeout):
    cfg = build_cfg(CONFIGS[cname])
    result = infogain.run(prompt["problem"], cfg)
    verdict = adjudicator.evaluate_case(prompt, result, judge_model=judge_model, timeout=judge_timeout)
    b = result["bucket"]
    u = result["usage"]
    vals = [r.get("value", 0) for r in b]
    return {
        "prompt": prompt["id"], "config": cname, "rep": rep,
        "run_judge": cfg["value_judge_model"], "adjudicator": judge_model,
        "bucket": len(b),
        "distinct_targets": len({(r.get("target") or "").lower() for r in b}),
        "top_value": round(max(vals), 3) if vals else 0.0,
        "mean_value": round(statistics.mean(vals), 3) if vals else 0.0,
        "n_pre_answer": sum(1 for r in b if r.get("recommendation") == "PRE_ANSWER"),
        "calls": u["calls"], "in_tok": u["input_tokens"], "out_tok": u["output_tokens"],
        "total_tok": u["input_tokens"] + u["output_tokens"],
        "wall_s": u["wall_seconds"], "model_s": round(u.get("model_seconds", 0), 1),
        "rounds": result["rounds_used"], "candidates": result["candidates_considered"],
        "acceptable": verdict["acceptable"],
        "judge_error": (verdict.get("judged") or {}).get("error"),
        "framing_accuracy": _crit(verdict, "framing_accuracy"),
        "question_relevance": _crit(verdict, "question_relevance"),
        "value_justified": _crit(verdict, "value_justified"),
        "diversity": _crit(verdict, "diversity"),
        "calibration": _crit(verdict, "calibration"),
    }


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--out", help="Write results JSON here (also printed to stdout).")
    p.add_argument("--prompt-ids", nargs="*", help="Subset of prompt ids (default all).")
    p.add_argument("--configs", nargs="*", help="Subset of config names (default all).")
    p.add_argument("--reps-breadth", type=int, help="Override breadth repetitions.")
    p.add_argument("--judge-model", default="deepseek", help="Adjudicator model alias.")
    p.add_argument("--judge-timeout", type=int, default=200)
    args = p.parse_args(argv)

    prompts = [x for x in PROMPTS if not args.prompt_ids or x["id"] in args.prompt_ids]
    configs = [c for c in CONFIGS if not args.configs or c in args.configs]
    reps = dict(DEFAULT_REPS)
    if args.reps_breadth is not None:
        reps["breadth-fast"] = args.reps_breadth

    rows = []
    t0 = time.time()
    for pr in prompts:
        for cname in configs:
            for rep in range(reps.get(cname, 1)):
                tag = f"{pr['id']}/{cname}/rep{rep}"
                print(f"… running {tag}", file=sys.stderr, flush=True)
                try:
                    row = run_cell(pr, cname, rep, args.judge_model, args.judge_timeout)
                except Exception as e:  # keep the matrix going on a single cell failure
                    row = {"prompt": pr["id"], "config": cname, "rep": rep, "error": str(e)}
                rows.append(row)
                print(f"  ✓ {tag}: bucket={row.get('bucket')} acceptable={row.get('acceptable')} "
                      f"tok={row.get('total_tok')} wall={row.get('wall_s')}s", file=sys.stderr, flush=True)
                if args.out:  # incremental save — survive a container/process restart
                    with open(args.out, "w") as f:
                        json.dump({"rows": rows, "n_cells": len(rows), "partial": True,
                                   "elapsed_s": round(time.time() - t0, 1)}, f, indent=2, default=str)

    out = {"rows": rows, "n_cells": len(rows), "partial": False,
           "elapsed_s": round(time.time() - t0, 1)}
    payload = json.dumps(out, indent=2, default=str)
    if args.out:
        with open(args.out, "w") as f:
            f.write(payload)
        print(f"wrote {args.out} ({len(rows)} cells, {out['elapsed_s']}s)", file=sys.stderr)
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
