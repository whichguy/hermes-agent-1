import json
import os

from engine import flow


def observer(event):
    # out-of-band narrator: append each lifecycle event to a log file.
    with open(os.environ["OBS_LOG"], "a") as f:
        f.write(json.dumps({"phase": event["phase"], "key": event.get("key")}, sort_keys=True) + "\n")


@flow(id="obs1")
def main(ctx, inp):
    a = ctx.step("a", lambda: 1)
    go = ctx.ask("gate", {"prompt": "?"})
    b = ctx.step("b", lambda: 2)
    return {"a": a, "b": b, "go": go}
