"""l_inhash — engine-level in-hash memo validity: a memoized step replays only if the demanded
`in_hash` matches the recorded one; a mismatch journals `memo_invalidated`, truncates the stale
key_order tail (so the walk may legitimately diverge), and re-executes — newest valid wins."""
import os

from engine import flow

H = os.environ.get("INHASH", "A")


@flow(id="l_inhash")
def main(ctx, inp):
    v = ctx.step("work", lambda: {"v": H}, in_hash="sha256:" + H)
    w = ctx.step("after", lambda: {"w": v["v"] + "!"})          # no in_hash: legacy,unconditional
    return {"got": v, "after": w}
