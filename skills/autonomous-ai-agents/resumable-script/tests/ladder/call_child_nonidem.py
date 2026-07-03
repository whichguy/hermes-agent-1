"""call_child_nonidem — a ctx.call CHILD with one non-idempotent step, deduped on the idem_key
into $CALL_LEDGER; $CALL_CRASH=1 hard-kills the process mid-step (crash-window scenario, mirrors
e3_crash.py / wf_nonidem.py). Used by call_parent_crashboundary."""
import os

from engine import flow


def act(idem_key):
    ledger = os.environ["CALL_LEDGER"]
    seen = set()
    if os.path.exists(ledger):
        seen = set(x for x in open(ledger).read().split("\n") if x)
    if idem_key not in seen:
        with open(ledger, "a") as f:
            f.write(idem_key + "\n")
    if os.environ.get("CALL_CRASH") == "1":
        os._exit(137)
    return {"acted": True}


@flow(id="child_nonidem")
def main(ctx, inp):
    v = ctx.step("act", act, idempotent=False)
    return {"v": v}
