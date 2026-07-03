"""wf_intervene_multi — MULTI-round enriched-context interruptibility: the model asks TWICE
(two separate suspend/resume round-trips) before resolving on the third call. Proves the
reentrancy claim in full: each already-completed round's model call is a memoized sub-step
that does NOT re-invoke the caller when a LATER round suspends and is resumed — not just
across a single interruption (see wf_intervene), but across a chain of them."""
from workflow import load_workflow

SPEC = {
    "id": "wf_intervene_multi", "version": 1, "start": "assess",
    "states": {
        "assess": {"prompt": "Decide something.", "intent": "assess", "next": "@done"},
    },
}


def _decisions(convo):
    return sum(1 for m in convo if "The human answered:" in (m.get("content") or ""))


def stub_llm(convo):
    # Round 0 and round 1 both ask (ASK: line); only round 2 (after TWO human answers) resolves.
    n = _decisions(convo)
    if n < 2:
        return "ASK: Round %d: continue?" % n
    return '{"resolved": true, "rounds": %d}' % n


flow = load_workflow(SPEC, {}, llm=stub_llm)
