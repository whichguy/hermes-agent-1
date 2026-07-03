from engine import flow


def _make_flaky():
    state = {"n": 0}

    def f(idem=None):
        state["n"] += 1
        if state["n"] < 3:
            raise RuntimeError("transient failure %d" % state["n"])
        return "ok"

    return f


@flow(id="l07")
def main(ctx, inp):
    v = ctx.step("flaky", _make_flaky(), retries=3, backoff_ms=1)
    return {"v": v}
