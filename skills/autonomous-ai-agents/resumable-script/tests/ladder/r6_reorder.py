import os

from engine import flow


@flow(id="r6")
def main(ctx, inp):
    # Shared prefix X, then A,B vs B,A: a real positional reorder (diverges at request #1).
    ctx.step("X", lambda: 0)
    if os.environ.get("ORDER") == "swapped":
        ctx.step("B", lambda: 2)
        ctx.step("A", lambda: 1)
    else:
        ctx.step("A", lambda: 1)
        ctx.step("B", lambda: 2)
    ctx.ask("gate", {"prompt": "?"})
    return {}
