"""wf_scaffold — the engine-owned prompt scaffolding is a fixed, auditable contract (v2).

A capturing stub encodes what it SAW into its results so the rung can assert the convo shape
directly: exactly one leading `system` message carrying the JSON-result rule + the ASK rule + the
outcomes block (with `means` text, routes object form) when routed; the author's rendered directive
arrives as the final `user` message BYTE-EXACT (never mutated); an UNROUTED step can interrupt via
the `ASK:` line convention; the resume turn uses the standardized _HUMAN_ANSWER wording; and the
ROUTER (independent judge) picks the edge.
"""
from workflow import load_workflow

SPEC = {
    "id": "wf_scaffold", "version": 1, "start": "probe",
    "states": {
        "probe": {"prompt": "Probe ${$.input.n}.",
                  "routes": {"go": {"to": "gated", "means": "the probe succeeded"}}},
        "gated": {"prompt": "Ask me."},              # unrouted — falls to @done (last state)
    },
}


def stub_llm(convo):
    sys, user = convo[0], convo[-1]
    if user["content"].startswith("Probe"):
        return {
            "outcome": "go",                                     # fast path: self-declared exit
            "n_msgs": len(convo),
            "sys_role": sys["role"],
            "sys_has_json_rule": "single JSON object" in sys["content"],
            "sys_has_ask_rule": "ASK: " in sys["content"],
            "sys_contract_leads": sys["content"].startswith("EXPECTED OUTPUT"),
            "sys_has_outcomes": "- go: the probe succeeded" in sys["content"],
            "user_exact": user["content"],
        }
    if "The human answered:" in user["content"]:
        return {"weave": user["content"]}
    return "ASK: what now?"                          # string reply -> the ASK: line convention


def stub_router(convo):
    sys, user = convo[0], convo[-1]
    ok = ("routing judge" in sys["content"]
          and "- go: the probe succeeded" in user["content"]
          and "Probe 42." in user["content"])
    return '{"outcome": "go"}' if ok else '{"outcome": "ask", "question": "router convo malformed"}'


flow = load_workflow(SPEC, {}, llm=stub_llm, router=stub_router)
