"""wf_decide — human decision gate (typed options) routed through `routes`."""
from workflow import load_workflow

SPEC = {
    "id": "wf_decide", "version": 1, "start": "review",
    "states": {
        "review": {"ask": "Approve ${in}?", "intent": "human review",
                   "options": ["approve", "deny"],
                   "routes": {"approve": "ok", "deny": "@fail"}},
        "ok": {"run": "finish", "next": "@done"},
    },
}


def finish(flowing, state):
    return {"approved": True, "decision": (state.get("review") or {}).get("decision")}


REGISTRY = {"finish": finish}
flow = load_workflow(SPEC, REGISTRY)
