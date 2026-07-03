import json
import os

from engine import flow


def observer(event):
    with open(os.environ["OBS_LOG"], "a") as f:
        f.write(json.dumps({"phase": event["phase"], "key": event.get("key")}, sort_keys=True) + "\n")


def _make_flaky():
    state = {"n": 0}

    def f(idem=None):
        state["n"] += 1
        if state["n"] < 2:
            raise RuntimeError("transient")
        return "ok"

    return f


@flow(id="obs3")
def main(ctx, inp):
    v = ctx.step("flaky", _make_flaky(), retries=1, backoff_ms=1)
    return {"v": v}
