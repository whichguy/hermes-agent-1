"""wf_seq — SEQUENTIAL FALL-THROUGH routing + the binding-routes/optional escape.

An unrouted state proceeds to the next state in DECLARATION ORDER; the last declared state falls to
`@done` (linear flows need zero routing keys). Routes stay binding: `b`'s "special" answer routes
explicitly, while its "go" answer is UNMAPPED — legal only because the step says `"optional": true` —
and falls onward to `c`.
"""
from workflow import load_workflow

SPEC = {
    "id": "wf_seq", "version": 1,
    "start": "a",
    "states": {
        "a": {"run": "mk"},                                          # no routing -> falls to b
        "b": {"ask": "Pick a lane.", "options": ["go", "special"],
              "routes": {"special": "z"}, "optional": True},         # "go" unmapped -> falls to c
        "c": {"run": "mk"},                                          # falls to z
        "z": {"run": "fin"},                                         # last declared -> @done
    },
}


def mk(flowing, state):
    return {"ok": True}


def fin(flowing, state):
    return {"done": True}


flow = load_workflow(SPEC, {"mk": mk, "fin": fin})
