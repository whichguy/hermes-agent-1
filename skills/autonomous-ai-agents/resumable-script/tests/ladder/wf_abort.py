"""wf_abort — a `when` predicate routes to the @fail terminal (-> clean failed)."""
from workflow import load_workflow

SPEC = {
    "id": "wf_abort", "version": 1, "start": "check",
    "states": {
        "check": {"run": "gate", "intent": "gate",
                  "when": [{"if": "$.check.bad", "to": "@fail"}], "next": "@done"},
    },
}


def gate(flowing, state):
    return {"bad": bool((flowing or {}).get("bad", False))}


REGISTRY = {"gate": gate}
flow = load_workflow(SPEC, REGISTRY)
