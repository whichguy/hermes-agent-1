from engine import flow


def observer(event):
    raise RuntimeError("observer boom")   # must NOT affect the flow (try-guarded in the engine)


@flow(id="obs2")
def main(ctx, inp):
    v = ctx.step("x", lambda: 1)
    return {"v": v}
