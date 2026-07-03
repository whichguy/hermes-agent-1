"""wf_mapsusp — durability of a step INSIDE a map: an inner `ask` suspends per item; resume must
memoize already-answered items and advance (never re-ask item 0)."""
from workflow import load_workflow

SPEC = {
    "id": "wf_mapsusp", "version": 1, "start": "gate_each",
    "states": {
        "gate_each": {"map": {"over": "$.input.xs", "as": "x",
                              "do": {"ask": "Approve ${$.x}?", "options": ["approve", "reject"]}},
                      "intent": "approve each item", "next": "@done"},
    },
}


flow = load_workflow(SPEC, {})
