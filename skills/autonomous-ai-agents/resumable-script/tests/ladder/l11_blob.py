from engine import flow


@flow(id="l11blob")
def main(ctx, inp):
    big = ctx.step("big", lambda: "x" * 70000)
    return {"len": len(big)}
