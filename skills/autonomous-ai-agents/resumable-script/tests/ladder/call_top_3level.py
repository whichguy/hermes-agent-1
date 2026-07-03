"""call_top_3level — a 3-level ctx.call chain (top -> mid -> leaf), where mid ALSO has its own
gate after the leaf resolves — so the suspend depth changes between resumes (3, then 2)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import call_mid_wraps_leaf
from engine import flow


@flow(id="top_3level")
def main(ctx, inp):
    r = ctx.call("child", call_mid_wraps_leaf.main, None)
    return {"from_child": r}
