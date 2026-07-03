from engine import flow


@flow(id="lhelpers")
def main(ctx, inp):
    # Memoized nondeterminism helpers + the wait() gate. Values are captured on the
    # first run and replayed verbatim, so the returned booleans are deterministic.
    t1 = ctx.now()
    r1 = ctx.random()
    u1 = ctx.uuid()
    go = ctx.wait("gate", {"prompt": "proceed?"})
    return {"has_time": isinstance(t1, (int, float)),
            "has_rand": 0.0 <= r1 <= 1.0,
            "has_uuid": isinstance(u1, str) and len(u1) > 0,
            "go": go}
