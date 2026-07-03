import os
import time

from engine import flow


def _hold():
    go = os.environ["GO"]
    for _ in range(2000):       # ~20s ceiling; the test always releases well before
        if os.path.exists(go):
            return "released"
        time.sleep(0.01)
    raise RuntimeError("timeout waiting for GO")


@flow(id="g1")
def main(ctx, inp):
    # Holds the run lock (blocks inside this step) until the test creates GO.
    return {"held": ctx.step("hold", lambda: _hold())}
