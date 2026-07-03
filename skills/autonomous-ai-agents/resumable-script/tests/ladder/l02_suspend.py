from engine import flow


@flow(id="l02")
def main(ctx, inp):
    go = ctx.ask("confirm", {"prompt": "go?", "type": "boolean"})
    return {"go": go}
