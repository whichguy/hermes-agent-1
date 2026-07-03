import os

from engine import flow


def call_api():
    if os.environ.get("API_DOWN") == "1":
        raise RuntimeError("API unavailable")
    return {"ok": True}


@flow(id="e2")
def main(ctx, inp):
    # E2E: a step fails on a down dependency; fix it out-of-band; re-run re-attempts
    # ONLY the failed step (setup is memoized).
    a = ctx.step("setup", lambda: "ready")
    b = ctx.step("call-api", call_api)
    return {"setup": a, "api": b}
