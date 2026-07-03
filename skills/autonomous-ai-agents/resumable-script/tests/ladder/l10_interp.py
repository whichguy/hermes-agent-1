from engine import flow


@flow(id="l10i")
def main(ctx, inp):
    go = ctx.ask("confirm", {"prompt": "expose it?", "type": "boolean"})
    if go:
        x = ctx.step("expose", lambda: "exposed")
    else:
        x = ctx.step("keep-private", lambda: "private")
    return {"x": x, "go": go}


def interpreter(req):
    """Map a free-form human reply to the boolean the flow expects."""
    raw = (req.get("raw") or "").lower()
    return any(w in raw for w in ("yes", "yeah", "sure", "do it", "go ahead", "expose"))
