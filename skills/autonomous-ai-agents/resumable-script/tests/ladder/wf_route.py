"""wf_route — predicate routing (`when`) vs the default `next`, driven by input. Covers all
three `when.if` forms: a bare `$`-path (truthy), a `{path,eq}` comparison, and a registry
predicate function `(state, result) -> bool`."""
from workflow import load_workflow

SPEC = {
    "id": "wf_route", "version": 1, "start": "pick",
    "states": {
        "pick": {"run": "classify", "intent": "classify",
                 "when": [{"if": {"path": "$.pick.kind", "eq": "big"}, "to": "big"},
                          {"if": "is_huge", "to": "huge"}],
                 "next": "small"},
        "big": {"run": "mark_big", "next": "@done"},
        "small": {"run": "mark_small", "next": "@done"},
        "huge": {"run": "mark_huge", "next": "@done"},
    },
}


def classify(flowing, state):
    return {"kind": (flowing or {}).get("kind", "small")}


def is_huge(state, result):
    # registry predicate fn form of `when.if`: (state, result) -> bool
    return result.get("kind") == "huge"


def mark_big(flowing, state):
    return {"size": "BIG"}


def mark_small(flowing, state):
    return {"size": "small"}


def mark_huge(flowing, state):
    return {"size": "HUGE"}


REGISTRY = {"classify": classify, "is_huge": is_huge, "mark_big": mark_big,
           "mark_small": mark_small, "mark_huge": mark_huge}
flow = load_workflow(SPEC, REGISTRY)
