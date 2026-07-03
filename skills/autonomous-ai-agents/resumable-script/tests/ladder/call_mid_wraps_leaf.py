"""call_mid_wraps_leaf — a ctx.call CHILD that itself calls another child (call_leaf_gate), THEN
has its own direct ctx.ask. Used by call_top_3level to prove the suspend depth can get SHALLOWER
between resumes (leaf gate first, at depth 3 from the true top; this level's own gate second, at
depth 2) — not just deeper."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import call_leaf_gate
from engine import flow


@flow(id="mid_wraps_leaf")
def main(ctx, inp):
    r = ctx.call("leaf", call_leaf_gate.main, None)
    ans = ctx.ask("mid_gate", {"prompt": "mid gate?", "options": ["ok"]})
    return {"from_leaf": r, "mid_ans": ans}
