from engine import flow


@flow(id="g3a")
def main(ctx, inp):
    # A step result outside the JSON-safe range must fail loudly (with the key), not crash.
    return {"big": ctx.step("oops", lambda: 2 ** 60)}
