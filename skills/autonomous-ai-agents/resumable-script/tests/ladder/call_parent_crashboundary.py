"""call_parent_crashboundary — a top-level (FileStore/CLI-backed) flow whose ctx.call child has a
non-idempotent step that can hard-crash mid-step ($CALL_CRASH=1). Demonstrates the accepted
crash-safety trade-off: a crash inside a ctx.call child leaves NO durable record at all (the
child is always MemoryStore-backed; Context.call hasn't unwound to append call_suspended yet),
unlike an equivalent TOP-LEVEL non-idempotent step under FileStore (see wf_nonidem/l08n, which
correctly escalate to in-doubt instead)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import call_child_nonidem
from engine import flow


@flow(id="parent_crashboundary")
def main(ctx, inp):
    r = ctx.call("risky_child", call_child_nonidem.main, None)
    return {"r": r}
