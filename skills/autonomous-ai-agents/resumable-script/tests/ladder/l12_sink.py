from engine import flow


def _make_flaky():
    state = {"n": 0}

    def f(idem=None):
        state["n"] += 1
        if state["n"] < 3:
            raise RuntimeError("transient %d" % state["n"])
        return "ok"

    return f


@flow(id="l12")
def main(ctx, inp):
    region = ctx.step("region", lambda: "us")
    plan = ctx.ask("plan", {"prompt": "which plan?", "type": "string"})
    items = ctx.step("items", lambda: ["a", "b", "c"])
    processed = []
    for it in items:
        processed.append(ctx.step("proc:%s" % it, lambda it=it: it + "!"))
    flaky = ctx.step("flaky", _make_flaky(), retries=2, backoff_ms=1)
    commit = ctx.ask("confirm", {"prompt": "commit?", "type": "boolean"})
    if commit:
        final = ctx.step("commit", lambda: "committed")
    else:
        final = ctx.step("rollback", lambda: "rolled-back")
    return {"region": region, "plan": plan, "processed": processed,
            "flaky": flaky, "final": final}
