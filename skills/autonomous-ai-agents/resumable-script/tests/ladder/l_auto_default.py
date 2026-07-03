from engine import flow


@flow(id="lauto")
def main(ctx, inp):
    # schema carries a default, so a headless (--auto) run resolves without suspending.
    go = ctx.ask("confirm", {"prompt": "proceed?"}, {"type": "boolean", "default": True})
    return {"go": go}
