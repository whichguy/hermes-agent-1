"""call_parent_fails — a top-level flow whose ctx.call child always fails. Proves a failed (not
suspended) child surfaces as FlowError(step=<call key>) at the parent, exactly like an ordinary
failed ctx.step."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import call_child_fails
from engine import flow


@flow(id="parent_fails")
def main(ctx, inp):
    r = ctx.call("risky_child", call_child_fails.main, None)
    return {"r": r}
