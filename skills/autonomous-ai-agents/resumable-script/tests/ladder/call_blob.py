"""call_blob — a ctx.call CHILD whose step result exceeds BLOB_THRESHOLD (65536), then gates.
Used by rf_child_blob to prove a GENUINE blob spill inside a child survives the portable-state
embedding (child_state.blobs inlines the value) and a cross-process resume. Module fixture (not
inline) because the cross-process leg goes through _call_driver.py, which imports by module name."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import flow


@flow(id="blob_leaf")
def blob_leaf(ctx, inp):
    big = ctx.step("big", lambda: "x" * 70000)
    ans = ctx.ask("gate", {"prompt": "big done?", "options": ["ok"]})
    return {"big_len": len(big), "head": big[:3], "ans": ans}


@flow(id="blob_parent")
def main(ctx, inp):
    r = ctx.call("child", blob_leaf, None)
    return {"from_child": r}
