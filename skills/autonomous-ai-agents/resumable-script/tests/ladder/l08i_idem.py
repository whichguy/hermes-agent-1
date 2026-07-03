from engine import flow


@flow(id="l08i")
def main(ctx, inp):
    v = ctx.step("act", lambda: "done", idempotent=True)
    return {"v": v}
