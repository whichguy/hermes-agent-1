from engine import flow


@flow(id="l05")
def main(ctx, inp):
    pick = ctx.ask("pick", {"prompt": "a or b?", "type": "string"})
    if pick == "a":
        v = ctx.step("branch-a", lambda: "A")
    else:
        v = ctx.step("branch-b", lambda: "B")
    return {"v": v}
