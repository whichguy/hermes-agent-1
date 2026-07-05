#!/usr/bin/env python3
"""validate_wrapper.py — #21 end-to-end: does the wrapper beat a no-clarification baseline?

For each prompt, produce two final responses with the iterate wrapper:
  - baseline : respond directly, answering NO clarifying questions
  - wrapper  : answer the top-K ranked questions via grounded research, then respond
A blind judge (randomized A/B order to kill position bias) picks which better serves the user.
Reframed from top-K-vs-bottom-K (the ranking is already validated via realized_change; the open
question is the product one — does the loop help?). Optional --with-bottom adds the ranking check.

Must run INSIDE the hermes container (grounded answerer shells out to `hermes`). Incremental,
append-across-invocations writes (run per-prompt to survive a container restart):

  docker exec -e OLLAMA_URL=http://host.docker.internal:11434/api/chat -e HERMES_HOME=/opt/data hermes \
    /opt/hermes/.venv/bin/python <this> --ids add-auth --k 1 --out /opt/data/wrapper_validation.json
"""

import argparse
import json
import os
import random
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))            # investigator/evals
_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))     # investigator/scripts -> iterate
# pipeline + testbank live in the next-best-questions ranker skill (this skill depends on it);
# try the new skill name first, fall back to the pre-rename name:
_NBQ_CANDIDATES = [os.path.join(_HOME, "skills", "autonomous-ai-agents", name, "scripts")
                   for name in ("next-best-questions", "information-gain")]
_INFOGAIN = os.environ.get("INFOGAIN_SCRIPTS_DIR") or next(
    (p for p in _NBQ_CANDIDATES if os.path.isdir(p)), _NBQ_CANDIDATES[0])
sys.path.insert(0, _INFOGAIN)                                 # pipeline
sys.path.insert(0, os.path.join(_INFOGAIN, "..", "evals"))   # testbank

import iterate  # noqa: E402
import pipeline  # noqa: E402
import testbank  # noqa: E402


MATERIAL_RISKS_SECTION = "Material risks — assumptions to confirm"
DEFAULT_STAKES_IDS = [
    "whatsapp-send", "slack-announce", "add-auth", "deploy-app", "portfolio-check", "buy-rent",
]
DEFAULT_GUARDRAIL_IDS = [
    "gmail-triage", "cal-schedule", "explain-oauth", "review-pr", "organize-files",
    "summarize-pdf", "plan-day", "security-audit",
]


def judge(problem, resp_a, resp_b, model, timeout):
    p = ("Two responses, A and B, to the SAME task. Which better serves the user — more correct, "
         "specific, actionable, and appropriately scoped? Penalize vagueness, padding, and wrong "
         "assumptions; reward responses that resolve real uncertainty.\n\n"
         f"TASK:\n{problem}\n\nRESPONSE A:\n{resp_a}\n\nRESPONSE B:\n{resp_b}\n\n"
         'Return ONLY JSON: {"winner": "A", "reason": "one sentence"}  (winner ∈ A|B|tie).')
    obj, _ = pipeline._call_json(model, p, timeout, num_predict=220)
    return obj if isinstance(obj, dict) else {"winner": "tie", "reason": "judge parse error"}


def _blind(problem, left, right, left_name, right_name, rng, model, timeout):
    """Judge left vs right with randomized presentation; return (winner_name, reason)."""
    flip = rng.random() < 0.5
    a, b = (right, left) if flip else (left, right)
    a_name, b_name = (right_name, left_name) if flip else (left_name, right_name)
    v = judge(problem, a, b, model, timeout)
    w = (v.get("winner") or "tie").strip().upper()[:1]
    name = {"A": a_name, "B": b_name}.get(w, "tie")
    return name, v.get("reason", "")


def run_prompt(pr, k, judge_model, timeout, with_bottom, prog, cfg=None):
    base = iterate.validate_selection(pr["problem"], "baseline", 0, cfg=cfg, progress=prog)
    top = iterate.validate_selection(pr["problem"], "top", k, cfg=cfg, progress=prog)
    rng = random.Random(hash(pr["id"]) & 0xFFFF)  # deterministic per-prompt order
    win, reason = _blind(pr["problem"], base["final"], top["final"], "baseline", "wrapper",
                         rng, judge_model, timeout)
    row = {
        "prompt": pr["id"], "cat": pr.get("cat"), "k": k,
        "winner": win, "reason": reason,
        "top_selected": top.get("selected"), "top_values": top.get("values"),
        "top_answered": sum(1 for t in top["tombstones"] if t["status"] == "ANSWERED"),
        "top_gaps": sum(1 for t in top["tombstones"] if t["status"] == "NOT_FOUND"),
        "baseline_len": len(base["final"]), "wrapper_len": len(top["final"]),
        "baseline_final": base["final"], "wrapper_final": top["final"],
    }
    if with_bottom:
        bot = iterate.validate_selection(pr["problem"], "bottom", k, progress=prog)
        rw, rr = _blind(pr["problem"], bot["final"], top["final"], "bottom", "top",
                        rng, judge_model, timeout)
        row["rank_winner"], row["rank_reason"] = rw, rr  # top vs bottom (ranking check)
    return row


def _majority_verdict(votes):
    """Return the strict majority of three votes, or tie when no option has two votes."""
    for candidate in ("stakes-on", "stakes-off", "tie"):
        if votes.count(candidate) >= 2:
            return candidate
    return "tie"


def run_prompt_stakes_ab(pr, k, judge_model, timeout, prog, cfg=None, answerer=None,
                         responder=None, arm_tag="key-gap"):
    """Compare stakes-aware responder prompts over one fixed answer/evidence phase."""
    cfg = {**iterate.DEFAULTS, **(cfg or {})}
    answerer = answerer or iterate.grounded_answer
    responder = responder or iterate.respond

    ranked = iterate.rank(pr["problem"], [], iterate._rank_cfg()) if k > 0 else []
    sel = ranked[:k]
    prog(f"stakes-ab-{k}: "
         + (" | ".join(q.get("question", "")[:40] for q in sel) or "(no clarification)"))
    tombstones, evidence = [], []
    for q in sel:
        found, text = answerer(q, pr["problem"], evidence, cfg)
        tombstones.append(iterate._tombstone(q, found, text))
        evidence = [t["evidence"] for t in tombstones]

    cfg_off = {**cfg, "stakes_aware_respond": False}
    resp_off = responder(pr["problem"], evidence, cfg_off)
    unresolved = iterate._unresolved_key_questions(tombstones, cfg)
    cfg_on = {**cfg, "stakes_aware_respond": True, "tombstones": tombstones,
              "unresolved_key_questions": unresolved}
    resp_on = responder(pr["problem"], evidence, cfg_on)

    fired = bool(unresolved)
    has_section = MATERIAL_RISKS_SECTION.lower() in resp_on.lower()
    off_has_section = MATERIAL_RISKS_SECTION.lower() in resp_off.lower()
    mechanical_pass = ((fired and has_section)
                       or (not fired and not has_section and not off_has_section))

    votes, reasons = [], []
    if fired:
        rng = random.Random(hash(pr["id"]) & 0xFFFF)
        for _ in range(3):
            winner, reason = _blind(pr["problem"], resp_off, resp_on,
                                    "stakes-off", "stakes-on", rng, judge_model, timeout)
            votes.append(winner)
            reasons.append(reason)

    return {
        "prompt": pr["id"], "cat": pr.get("cat"), "k": k, "arm_tag": arm_tag,
        "fired": fired, "mechanical_pass": mechanical_pass, "has_section": has_section,
        "winner": _majority_verdict(votes) if fired else None,
        "judge_votes": votes, "judge_reasons": reasons,
        "selected": [q.get("question") for q in sel],
        "values": [round(q.get("value", 0), 3) for q in sel],
        "answered": sum(1 for t in tombstones if t["status"] == "ANSWERED"),
        "gaps": sum(1 for t in tombstones if t["status"] == "NOT_FOUND"),
        "unresolved_key_questions": unresolved, "tombstones": tombstones,
        "stakes_off_len": len(resp_off), "stakes_on_len": len(resp_on),
        "stakes_off_final": resp_off, "stakes_on_final": resp_on,
    }


def _stakes_ab_summary(rows):
    stakes_rows = [r for r in rows if r.get("arm_tag") in ("key-gap", "guardrail")]
    key_rows = [r for r in stakes_rows if r.get("arm_tag") == "key-gap" and "error" not in r]
    guardrail_rows = [r for r in stakes_rows
                      if r.get("arm_tag") == "guardrail" and "error" not in r]
    key_fired = [r for r in key_rows if r.get("fired")]
    key_not_fired = [r for r in key_rows if not r.get("fired")]
    on_wins = sum(1 for r in key_fired if r.get("winner") == "stakes-on")
    off_wins = sum(1 for r in key_fired if r.get("winner") == "stakes-off")
    ties = sum(1 for r in key_fired if r.get("winner") == "tie")
    ratio = (str(round(on_wins / off_wins, 2)) if off_wins else
             ("∞" if on_wins else "n/a"))
    mechanical_failures = [r for r in stakes_rows if r.get("mechanical_pass") is False]
    guardrail_fired = [r for r in guardrail_rows if r.get("fired")]
    regressions = [r for r in guardrail_fired if r.get("winner") == "stakes-off"]

    print("\n=== stakes-aware responder A/B ===")
    print(f"key-gap precondition: fired {len(key_fired)} · did not fire {len(key_not_fired)}")
    print(f"fired key-gap verdicts: stakes-on {on_wins} · stakes-off {off_wins} · tie {ties}")
    print(f"stakes-on wins {on_wins} : stakes-off wins {off_wins} "
          f"(ratio {ratio}, n={len(key_fired)})")
    if mechanical_failures:
        names = ", ".join(r["prompt"] for r in mechanical_failures)
        print(f"MECHANICAL CHECK FAILED (BUG SIGNAL): {names}")
    else:
        print("mechanical checks: all passed")
    print(f"guardrail precondition: fired {len(guardrail_fired)} of {len(guardrail_rows)}")
    if regressions:
        print("GUARDRAIL REGRESSION (stakes-off favored): "
              + ", ".join(r["prompt"] for r in regressions))
    else:
        print("guardrail regressions: none")
    for r in stakes_rows:
        if r.get("fired"):
            print(f"  {r['prompt']:<16} {r['arm_tag']:<9} majority={r.get('winner'):<10} "
                  f"votes={r.get('judge_votes', [])}")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=("wrapper", "stakes-ab"), default="wrapper")
    p.add_argument("--ids", nargs="*", help="prompt ids from testbank (default: a small agentic set).")
    p.add_argument("--stakes-ids", nargs="*", default=DEFAULT_STAKES_IDS,
                   help="stakes-ab fixtures expected to expose key gaps.")
    p.add_argument("--guardrail-ids", nargs="*", default=DEFAULT_GUARDRAIL_IDS,
                   help="stakes-ab fixtures expected to remain no-ops.")
    p.add_argument("--k", type=int, default=1)
    p.add_argument("--judge-model", default="deepseek")
    p.add_argument("--timeout", type=int, default=200)
    p.add_argument("--with-bottom", action="store_true", help="also judge top-K vs bottom-K (ranking).")
    p.add_argument("--cwd", help="pin BOTH answerer and responder to this project dir (de-confounds).")
    p.add_argument("--responder-tools", default="", help="responder toolsets (e.g. 'file') for the test.")
    p.add_argument("--stakes-aware-respond", action="store_true",
                   help="include unresolved key gaps in responder context.")
    p.add_argument("--key-gap-threshold", type=float,
                   default=iterate.DEFAULTS["key_gap_threshold"],
                   help="minimum unresolved question value for stakes-ab surfacing.")
    p.add_argument("--out")
    args = p.parse_args(argv)

    if args.mode == "stakes-ab":
        cfg = {"answer_cwd": args.cwd, "responder_cwd": args.cwd,
               "responder_toolsets": args.responder_tools,
               "key_gap_threshold": args.key_gap_threshold}
        tagged_ids = [(prompt_id, "key-gap") for prompt_id in args.stakes_ids]
        tagged_ids.extend((prompt_id, "guardrail") for prompt_id in args.guardrail_ids)
        prompts = []
        for prompt_id, arm_tag in tagged_ids:
            if prompt_id not in testbank.BY_ID:
                print(f"… warn: unknown prompt id {prompt_id!r}; skipping", file=sys.stderr)
                continue
            prompts.append((testbank.BY_ID[prompt_id], arm_tag))
        judge_model = pipeline.resolve_alias(args.judge_model)
        prog = lambda m: print(f"… {m}", file=sys.stderr, flush=True)

        rows = []
        if args.out and os.path.exists(args.out):
            try:
                rows = json.load(open(args.out)).get("rows", [])
            except Exception:
                rows = []
        done = {r["prompt"] for r in rows}

        t0 = time.time()
        for pr, arm_tag in prompts:
            if pr["id"] in done:
                print(f"… skip {pr['id']} (already in {args.out})", file=sys.stderr)
                continue
            print(f"… === {pr['id']} ({pr['cat']}; {arm_tag}) ===", file=sys.stderr, flush=True)
            try:
                row = run_prompt_stakes_ab(pr, args.k, judge_model, args.timeout, prog, cfg,
                                           arm_tag=arm_tag)
            except Exception as e:
                row = {"prompt": pr["id"], "cat": pr.get("cat"), "arm_tag": arm_tag,
                       "error": str(e)}
            rows.append(row)
            print(f"  -> fired={row.get('fired')} mechanical={row.get('mechanical_pass')} "
                  f"winner={row.get('winner', row.get('error'))}", file=sys.stderr)
            if args.out:
                with open(args.out, "w") as f:
                    json.dump({"rows": rows, "elapsed_s": round(time.time() - t0, 1)}, f,
                              indent=2, default=str)
        _stakes_ab_summary(rows)
        return 0

    cfg = {"answer_cwd": args.cwd, "responder_cwd": args.cwd,
           "responder_toolsets": args.responder_tools,
           "stakes_aware_respond": args.stakes_aware_respond}

    default_ids = ["research-compare", "add-auth", "gmail-triage"]  # researchable / spec / just-do-it
    ids = args.ids or default_ids
    prompts = [testbank.BY_ID[i] for i in ids if i in testbank.BY_ID]
    judge_model = pipeline.resolve_alias(args.judge_model)
    prog = lambda m: print(f"… {m}", file=sys.stderr, flush=True)

    rows = []
    if args.out and os.path.exists(args.out):  # append across per-prompt invocations
        try:
            rows = json.load(open(args.out)).get("rows", [])
        except Exception:
            rows = []
    done = {r["prompt"] for r in rows}

    t0 = time.time()
    for pr in prompts:
        if pr["id"] in done:
            print(f"… skip {pr['id']} (already in {args.out})", file=sys.stderr)
            continue
        print(f"… === {pr['id']} ({pr['cat']}) ===", file=sys.stderr, flush=True)
        try:
            row = run_prompt(pr, args.k, judge_model, args.timeout, args.with_bottom, prog, cfg)
        except Exception as e:
            row = {"prompt": pr["id"], "cat": pr.get("cat"), "error": str(e)}
        rows.append(row)
        print(f"  -> winner: {row.get('winner', row.get('error'))} "
              f"(answered={row.get('top_answered')}, gaps={row.get('top_gaps')})", file=sys.stderr)
        if args.out:
            with open(args.out, "w") as f:
                json.dump({"rows": rows, "elapsed_s": round(time.time() - t0, 1)}, f, indent=2, default=str)

    # summary
    real = [r for r in rows if "winner" in r]
    wins = sum(1 for r in real if r["winner"] == "wrapper")
    bl = sum(1 for r in real if r["winner"] == "baseline")
    tie = sum(1 for r in real if r["winner"] == "tie")
    print(f"\n=== wrapper vs baseline: wrapper {wins} · baseline {bl} · tie {tie}  (n={len(real)}) ===")
    for r in real:
        print(f"  {r['prompt']:<16} {r['cat']:<14} winner={r['winner']:<9} "
              f"answered={r.get('top_answered')} gaps={r.get('top_gaps')}  | {r.get('reason','')[:70]}")
        if "rank_winner" in r:
            print(f"      ranking (top vs bottom): {r['rank_winner']} | {r.get('rank_reason','')[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
