import os

from engine import flow


def charge(idem):
    # Side effect deduped on the idempotency key, then a HARD crash before the engine
    # can journal step_completed — simulating a real mid-step process death.
    ledger = os.environ["LEDGER"]
    seen = set()
    if os.path.exists(ledger):
        seen = set(x for x in open(ledger).read().split("\n") if x)
    if idem not in seen:
        with open(ledger, "a") as f:
            f.write(idem + "\n")
    if os.environ.get("CRASH") == "1":
        os._exit(137)
    return {"charged": True}


@flow(id="e3")
def main(ctx, inp):
    return ctx.step("charge", charge, idempotent=True)
