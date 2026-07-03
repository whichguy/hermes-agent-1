#!/usr/bin/env python3
"""Minimal copy-paste flow skeleton."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from engine import flow, run_cli  # noqa: E402


@flow(id="my-flow", version=1)
def my_flow(ctx, inp):
    # value  = ctx.step("step-1", lambda: do_something())
    # value2 = ctx.step("step-2", lambda idem: side_effect(idem), idempotent=False)
    # answer = ctx.ask("confirm", {"prompt": "proceed?", "type": "boolean"})
    return {}


if __name__ == "__main__":
    sys.exit(run_cli(my_flow))
