"""call_collision — two ctx.call sites reusing the SAME key in one pass. The child (call_child_ok)
resolves without suspending, so the flow genuinely reaches the second ctx.call in the same pass,
proving ctx.call funnels through the ordinary _request collision guard (no new logic needed)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import call_child_ok
from engine import flow


@flow(id="call_collision")
def main(ctx, inp):
    ctx.call("dup", call_child_ok.main, None)
    ctx.call("dup", call_child_ok.main, None)   # duplicate key in one pass -> KeyCollision
    return {}
