from engine import flow


@flow(id="eecho")
def main(ctx, inp):
    # E2E: input flows to the step, survives the suspend (read back from the journal),
    # and two runs in two state dirs stay independent.
    seen = ctx.step("record", lambda: inp)
    go = ctx.ask("confirm", {"prompt": "ok?"})
    return {"input": seen, "go": go}
