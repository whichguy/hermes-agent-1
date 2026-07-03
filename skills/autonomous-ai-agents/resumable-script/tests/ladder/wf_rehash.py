"""wf_rehash — the user's edit-while-parked scenario, end to end. $WF_REHASH selects the `edit`
step's TEMPLATE (v1/v2); $WF_REHASH_MEANS selects its route's `means` (m1/m2). Editing the template
changes the task convo -> that call re-executes AND its output changes -> the downstream `post`
step's rendered input changes -> it re-executes too (cascade). Editing only the means changes the
prefixed outcome contract -> the task re-executes, but its OUTPUT is identical -> `post` replays
untouched (the cascade follows OUTPUTS, not definitions). The gate's answer survives
either way; the untouched `pre` step always replays."""
import os

from workflow import load_workflow

V = os.environ.get("WF_REHASH", "v1")
M = os.environ.get("WF_REHASH_MEANS", "m1")
TEMPLATE = {"v1": "Assess ${in} carefully.", "v2": "Assess ${in} thoroughly."}[V]
MEANS = {"m1": "looks fine", "m2": "seems fine"}[M]

SPEC = {
    "id": "wf_rehash", "version": 1, "start": "pre",
    "states": {
        "pre": {"prompt": "Prep ${in}."},
        "edit": {"prompt": TEMPLATE,
                 "routes": {"go": {"to": "post", "means": MEANS}}},
        "post": {"prompt": "Post ${$.edit.tag}."},
        "gate": {"ask": "Ship it?", "options": ["ok", "no"],
                 "routes": {"ok": "fin", "no": "@fail"}},
        "fin": {"run": "fin"},
    },
}


def stub_llm(convo):
    # echo the rendered directive; the routed `edit` step declares its exit (fast path)
    return {"tag": convo[-1]["content"], "outcome": "go"}


def stub_router(convo):
    raise AssertionError("fast path: the judge must never be consulted")


def fin(flowing, state):
    return {"seen": state["post"]["tag"]}


flow = load_workflow(SPEC, {"fin": fin}, llm=stub_llm, router=stub_router)
