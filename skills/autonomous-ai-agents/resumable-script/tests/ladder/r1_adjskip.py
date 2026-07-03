from engine import flow


def _boom(idem=None):
    raise RuntimeError("kaboom")


@flow(id="r1")
def main(ctx, inp):
    v = ctx.step("risky", _boom, retries=0)        # fails -> adjudicator skips (must memoize)
    go = ctx.ask("gate", {"prompt": "continue?"})  # forces a suspend+resume after the skip
    return {"v": v, "go": go}


def adjudicator(req):
    if req.get("kind") == "step_failed":
        return {"action": "skip", "value": "skipped"}
    return {"action": "abort"}
