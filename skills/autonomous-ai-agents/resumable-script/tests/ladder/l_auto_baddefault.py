from engine import flow


@flow(id="lautobad")
def main(ctx, inp):
    # the schema default VIOLATES the schema — headless must reject it (exit 12), not memoize it.
    ans = ctx.ask("confirm", {"prompt": "proceed?"},
                  {"enum": ["yes", "no"], "default": True})
    return {"ans": ans}
