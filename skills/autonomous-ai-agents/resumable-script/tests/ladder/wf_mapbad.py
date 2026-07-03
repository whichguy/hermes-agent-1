"""wf_mapbad — a `map` whose `over` does not resolve to a list must fail cleanly with a clear message."""
from workflow import load_workflow

SPEC = {
    "id": "wf_mapbad", "version": 1, "start": "s",
    "states": {
        "s": {"map": {"over": "$.input.notalist", "as": "it", "do": {"run": "box"}}, "next": "@done"},
    },
}


def box(item, state):
    return item


flow = load_workflow(SPEC, {"box": box})
