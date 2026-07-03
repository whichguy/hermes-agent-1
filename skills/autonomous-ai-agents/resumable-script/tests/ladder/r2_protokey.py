from engine import flow


@flow(id="r2")
def main(ctx, inp):
    # Step keys that collide with Object.prototype member names must still execute
    # and memoize correctly (regression for the JS `{}`-prototype bug).
    a = ctx.step("constructor", lambda: "real-value")
    b = ctx.step("toString", lambda: "also-real")
    return {"a": a, "b": b}
