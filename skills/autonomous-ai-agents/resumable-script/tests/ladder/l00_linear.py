from engine import flow


@flow(id="l00")
def main(ctx, inp):
    a = ctx.step("a", lambda: 1)
    b = ctx.step("b", lambda: 2)
    c = ctx.step("c", lambda: a + b)
    return {"sum": c}
