from engine import flow


@flow(id="g3b")
def main(ctx, inp):
    ctx.step("ok", lambda: 1)
    raise RuntimeError("glue code blew up")     # an author bug in pure glue -> clean failed
