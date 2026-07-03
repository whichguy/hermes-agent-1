from engine import flow


@flow(id="l09")
def main(ctx, inp):
    a = ctx.step("a", lambda: 1)
    items = ctx.step("items", lambda: ["p", "q"])
    out = []
    for it in items:
        out.append(ctx.step("u:%s" % it, lambda it=it: it.upper()))
    ok = ctx.ask("confirm", {"prompt": "ok?", "type": "boolean"})
    return {"a": a, "out": out, "ok": ok}
