from engine import flow


@flow(id="l06c")
def main(ctx, inp):
    ctx.step("dup", lambda: 1)
    ctx.step("dup", lambda: 2)   # same key in one pass -> KeyCollision (exit 2)
    return {}
