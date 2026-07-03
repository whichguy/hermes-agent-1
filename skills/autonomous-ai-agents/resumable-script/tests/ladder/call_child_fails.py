"""call_child_fails — a ctx.call CHILD whose one step always throws, no retries. Used by
call_parent_fails to prove a failed (not suspended) child surfaces as an ordinary FlowError at
the parent's call site."""
from engine import flow


def boom():
    raise RuntimeError("child step exploded")


@flow(id="child_fails")
def main(ctx, inp):
    return ctx.step("boom", boom)
