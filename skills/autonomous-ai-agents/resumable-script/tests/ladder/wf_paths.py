"""wf_paths — end-to-end interpolation: index + missing->"" + nested + $${ escape in a rendered
prompt, and a lone-${...} mutation source that preserves the value's native (number) type."""
from workflow import load_workflow

SPEC = {
    "id": "wf_paths", "version": 1, "start": "gate",
    "states": {
        "gate": {"ask": "item=${$.input.items[1].name} missing=[${$.input.nope}] esc=$${x} price=${$.input.price}",
                 "intent": "render check", "options": ["go"], "routes": {"go": "done"}},
        "done": {"run": "fin", "set": {"$.saved": "${$.input.price}"}, "next": "@done"},
    },
}


def fin(flowing, state):
    return {"ok": True}


flow = load_workflow(SPEC, {"fin": fin})
