import os

from engine import flow


@flow(id="r5")
def main(ctx, inp):
    # GLUE (outside any step) re-runs on EVERY pass; the wrapped step runs once.
    with open(os.environ["GLUE_LOG"], "a") as f:
        f.write("pass\n")
    v = ctx.step("work", lambda: 1)
    go = ctx.ask("gate", {"prompt": "?"})
    return {"v": v, "go": go}
