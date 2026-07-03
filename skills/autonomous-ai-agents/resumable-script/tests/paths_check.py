#!/usr/bin/env python3
"""Cross-engine golden matrix for the workflow JSONPath resolver + ${...} interpolation (Python side).

Loads tests/paths_cases.json (the SAME file the JS runner uses) and drives each case against
workflow.py's resolver helpers, comparing the canonical-JSON of the result to the expected value.
Because both engines check the identical golden file, passing on both = byte-for-byte parity.

  python3 tests/paths_check.py
"""
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import engine       # noqa: E402
import workflow     # noqa: E402


def run_case(c):
    op = c["op"]
    if op == "get":
        root, toks = workflow._parse_path(c["path"])
        return workflow._resolve(c.get("state", {}), c.get("ret"), root, toks)
    if op == "render":
        return workflow._render_template(c["template"], c.get("state", {}), c.get("flowing"), c.get("ret"))
    if op == "value":
        return workflow._resolve_value(c["value"], c.get("state", {}), c.get("flowing"), c.get("ret"))
    if op == "apply":
        st = copy.deepcopy(c["state"])
        workflow._apply_ops(st, c.get("flowing"), c.get("ret"), c["ops"])
        return st
    if op == "pred":
        return workflow._eval_pred(c["cond"], {}, c.get("state", {}), c.get("result"))
    raise ValueError("unknown op %r" % op)


def main():
    with open(os.path.join(HERE, "paths_cases.json"), encoding="utf-8") as f:
        cases = json.load(f)
    fails = 0
    for c in cases:
        try:
            got = engine._dumps(run_case(c))
        except Exception as e:  # noqa: BLE001
            got = "EXC:%s" % e
        # An `expect` of "EXC:<message>" asserts BOTH engines raise the identical message (error-path
        # parity); otherwise compare canonical JSON of the value.
        exp = c["expect"]
        exp = exp if isinstance(exp, str) and exp.startswith("EXC:") else engine._dumps(exp)
        if got != exp:
            fails += 1
            sys.stderr.write("  FAIL %s: got %s expected %s\n" % (c["name"], got, exp))
    if fails:
        print("  paths_check.py: %d/%d FAILED" % (fails, len(cases)))
        return 1
    print("  PASS paths (%d cases)" % len(cases))
    return 0


if __name__ == "__main__":
    sys.exit(main())
