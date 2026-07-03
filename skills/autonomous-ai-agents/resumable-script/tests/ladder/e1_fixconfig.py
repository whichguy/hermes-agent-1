import os

from engine import flow


def read_config():
    path = os.environ["CFG"]
    if not os.path.exists(path):
        raise RuntimeError("config not set yet")
    return open(path).read().strip()


@flow(id="e1")
def main(ctx, inp):
    # E2E: ask the user to set up a precondition in the system, THEN read it on resume.
    ack = ctx.ask("fix-config", {"prompt": "Create the config file, then continue.", "type": "boolean"})
    cfg = ctx.step("read-config", read_config)
    return {"cfg": cfg, "ack": ack}
