"""call_wf_child — THE LAYER-COMPOSITION PIN: a code-first parent ctx.calls a WORKFLOW-SPEC child.

Every nested-call rung before this one used code-first children; every workflow-spec flow was only
ever top-level. This fixture composes the two: the child's spec walk (prep -> ASK-interrupting
prompt -> router -> ask gate -> fin) suspends TWICE inside the ctx.call boundary, and resume must
re-walk the embedded child journal — no re-issued model calls, no re-asked gates, state retained
across both pauses, the child's {result, state} memoized into the parent exactly once."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import call_wf_vetting
from engine import flow


@flow(id="call_wf_child")
def main(ctx, inp):
    r = ctx.call("vet", call_wf_vetting.child, inp)
    return {"vet": r}
