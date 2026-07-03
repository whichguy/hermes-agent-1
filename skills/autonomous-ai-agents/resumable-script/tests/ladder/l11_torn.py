from engine import flow


@flow(id="l11torn")
def main(ctx, inp):
    v = ctx.step("only", lambda: "v1")
    return {"v": v}
