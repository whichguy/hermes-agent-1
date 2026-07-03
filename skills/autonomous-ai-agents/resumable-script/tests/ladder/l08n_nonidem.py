from engine import flow


@flow(id="l08n")
def main(ctx, inp):
    v = ctx.step("act", lambda: "done", idempotent=False)
    return {"v": v}
