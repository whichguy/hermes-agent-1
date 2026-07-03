"""wf_intervene — enriched-context interruptibility, v2: the TASK replies with an `ASK:` line, the
human's answer is woven into the task convo (standardized wording), the re-called task resolves to a
discrete JSON object, and the ROUTER routes it."""
from workflow import load_workflow

SPEC = {
    "id": "wf_intervene", "version": 1, "start": "assess",
    "states": {
        "assess": {"prompt": "Assess ${in}.", "intent": "assess",
                   "set": {"$.via": "human"},
                   "routes": {"approve": "@done", "deny": "@fail"}},
    },
}


def stub_llm(convo):
    # First pass: interrupt via the ASK: line. After the answer is woven in, resolve.
    if any("The human answered:" in (m.get("content") or "") for m in convo):
        return '{"resolved": true}'
    return "ASK: Approve or deny?"


def stub_router(convo):
    return '{"outcome": "approve"}'


flow = load_workflow(SPEC, {}, llm=stub_llm, router=stub_router)
