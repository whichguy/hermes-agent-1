from engine import flow


@flow(id="l06")
def main(ctx, inp):
    items = ctx.step("items", lambda: ["x", "y", "z"])
    out = []
    for i, it in enumerate(items):
        if i == 2:
            ctx.ask("pause", {"prompt": "continue with last item?"})
        out.append(ctx.step("item:%s" % it, lambda it=it: it.upper()))
    return {"out": out}
