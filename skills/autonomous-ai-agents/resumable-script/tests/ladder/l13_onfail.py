import os

from engine import flow


def boom():
    raise RuntimeError("kaboom")


def policy(err, attempt):
    # on_fail policy: raise-mode (env) exercises provenance; default retries once then catches.
    if os.environ.get("ONFAIL_MODE") == "raise":
        return {"action": "raise"}
    if attempt < 2:
        return {"action": "retry"}
    return {"action": "catch"}


@flow(id="l13")
def main(ctx, inp):
    # The engine primitive under wf `on_error`: a caught failure memoizes an __error__
    # sentinel (synthesized step_completed), so replay re-takes the same branch.
    r = ctx.step("risky", boom, on_fail=policy)
    if isinstance(r, dict) and "__error__" in r:
        return {"caught": True, "name": r["__error__"]["name"],
                "attempts": r["__error__"]["attempts"]}
    return {"caught": False}
