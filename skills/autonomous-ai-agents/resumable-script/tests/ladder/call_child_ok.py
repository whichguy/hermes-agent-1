"""call_child_ok — a ctx.call CHILD with a single run step, no suspend at all. Used to build a
same-pass duplicate-call-key scenario (call_collision): a child that resolves immediately lets
the parent flow reach a SECOND ctx.call in the same pass instead of suspending on the first."""
from engine import flow


@flow(id="child_ok")
def main(ctx, inp):
    return ctx.step("noop", lambda: 42)
