import os

from engine import flow


def charge(idem):
    """A non-idempotent effect made safe by deduping on the idempotency key."""
    ledger = os.environ["LEDGER"]
    seen = set()
    if os.path.exists(ledger):
        seen = set(x for x in open(ledger).read().split("\n") if x)
    applied = False
    if idem not in seen:
        with open(ledger, "a") as f:
            f.write(idem + "\n")
        applied = True
    return {"applied_now": applied}


@flow(id="lidem")
def main(ctx, inp):
    # idempotent=True so an in-doubt dangling step re-runs; the downstream dedupes
    # on the stable idempotency key, so the effect is applied at most once.
    return ctx.step("charge", charge, idempotent=True)
