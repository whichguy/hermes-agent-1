"""wf_return — the SELF-DECLARED OUTCOME fast path on one routed prompt step: the TASK's first
reply is unparsable prose (-> the engine's JSON repair prompt), the repaired reply carries
"outcome": "approve" per the prefixed contract -> routed MECHANICALLY (zero judge calls); authored
`set` pulls from the parsed output via `${@...}`."""
from workflow import load_workflow

SPEC = {
    "id": "wf_return", "version": 1, "start": "assess",
    "states": {
        "assess": {"prompt": "Assess ${in}.", "intent": "assess the request",
                   "set": {"$.decision": "${@.verdict}"},
                   "routes": {"approve": "finish", "deny": "@fail"}},
        "finish": {"run": "finish", "next": "@done"},
    },
}


def stub_llm(convo):
    # First attempt: prose (not one JSON object). After the engine's repair prompt: discrete JSON
    # that DECLARES its exit per the prefixed contract.
    if any("not a single parsable JSON object" in (m.get("content") or "") for m in convo):
        return '{"verdict": "approve", "x": 1, "outcome": "approve"}'
    return "Let me think about this out loud instead of answering properly."


def stub_router(convo):
    raise AssertionError("fast path: the judge must never be consulted")


def finish(flowing, state):
    return {"ok": True, "decision": state.get("decision")}


REGISTRY = {"finish": finish}
flow = load_workflow(SPEC, REGISTRY, llm=stub_llm, router=stub_router)
