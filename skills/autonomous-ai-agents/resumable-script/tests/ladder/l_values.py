from engine import flow


@flow(id="lvalues")
def main(ctx, inp):
    # Tricky-but-JSON-safe values that must round-trip identically across engines:
    # unicode (incl. astral plane), a boundary-safe big int, nesting, bool, null.
    payload = ctx.step("payload", lambda: {
        "unicode": "héllo — 世界 🚀",
        "big": 9007199254740991,        # 2^53 - 1, the max safe integer
        "nested": {"a": [1, 2, {"b": True}], "c": None},
        "flag": False,
        "list": ["x", "y"],
    })
    return payload
