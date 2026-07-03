import os

from engine import flow


@flow(id="l11flip")
def main(ctx, inp):
    # Non-determinism: the step key depends on an env var that the test flips
    # between run and resume. The recorded key (run) then mismatches the replayed
    # key (resume) -> strict-replay divergence -> exit 3.
    flip = os.environ.get("FLIP", "a")
    v = ctx.step("k-%s" % flip, lambda: flip)
    ctx.ask("gate", {"prompt": "?"})
    return {"v": v}
