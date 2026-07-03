from engine import flow


@flow(id="l04")
def main(ctx, inp):
    x = ctx.ask("q1", {"prompt": "x?"})
    y = ctx.ask("q2", {"prompt": "y?"})
    return {"x": x, "y": y}
