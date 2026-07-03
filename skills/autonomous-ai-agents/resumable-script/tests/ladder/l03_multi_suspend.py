from engine import flow


@flow(id="l03")
def main(ctx, inp):
    a = ctx.step("a", lambda: 1)
    b = ctx.step("b", lambda: 2)
    ok = ctx.ask("confirm", {"prompt": "ok?", "type": "boolean"})
    c = ctx.step("c", lambda: a + b + (10 if ok else 0))
    return {"c": c}
