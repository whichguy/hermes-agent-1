"""call_top_2level — the minimal 2-level ctx.call chain (top -> child=leaf gate). CLI-driven
rungs use this via `engine.py --flow call_top_2level.py`; library-API rungs pass `main` directly
to run_flow/resume_flow."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import call_leaf_gate
from engine import flow


@flow(id="top_2level")
def main(ctx, inp):
    r = ctx.call("child", call_leaf_gate.main, None)
    return {"from_child": r}
