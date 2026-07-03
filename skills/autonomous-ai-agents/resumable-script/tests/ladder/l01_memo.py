from engine import flow


@flow(id="l01")
def main(ctx, inp):
    x = ctx.step("compute", lambda: 41 + 1)
    return {"x": x}
