import os

from engine import flow


def _boom(idem=None):
    raise RuntimeError("kaboom")


@flow(id="l10a")
def main(ctx, inp):
    v = ctx.step("risky", _boom, retries=0)
    return {"v": v}


def adjudicator(req):
    """Decide what to do with a failed step. ADJ_MODE selects the branch for tests."""
    if req.get("kind") == "step_failed":
        mode = os.environ.get("ADJ_MODE", "skip")
        if mode == "abort":
            return {"action": "abort"}
        if mode == "unknown":
            return {"action": "huh"}       # unrecognized -> must propagate the failure
        return {"action": "skip", "value": "skipped-by-adjudicator"}
    return {"action": "abort"}
