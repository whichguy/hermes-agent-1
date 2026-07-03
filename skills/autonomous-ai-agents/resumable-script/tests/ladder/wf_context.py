"""wf_context — conversational context is SHARED BY DEFAULT: a flow's model steps are one continuous
conversation (steps = directed prompts into a persistent agent context). Isolation is the OPT-OUT:
step-level `"context": "isolated"` drops a step out of the thread; spec-level `"context": "isolated"`
($WF_CONTEXT=lean) makes the whole flow lean. A gate loop-back proves the thread CONTINUES across
revisits, and GATES ARE TURNS (the question + the human's answer join the thread) unless the gate
itself opts out ($WF_CONTEXT=isogate). The stub llm reports how many messages it sees, so the rung asserts thread shape directly."""
import os

from workflow import load_workflow

VARIANT = os.environ.get("WF_CONTEXT", "")

SPEC = {
    "id": "wf_context", "version": 1, "start": "first",
    "states": {
        "first": {"prompt": "Say hi."},                                    # default: the flow thread
        "second": {"prompt": "Say more."},                                 # sees first's exchange
        "third": {"prompt": "Say hi in isolation.", "context": "isolated"},
        "fourth": {"prompt": "Back on the thread."},                       # sees first+second
        "gate": {"ask": "Again?", "options": ["again", "done"],
                 "routes": {"again": "fourth", "done": "@done"}},          # loop-back: thread grows
    },
}
if VARIANT == "lean":
    SPEC = dict(SPEC, context="isolated")
if VARIANT == "isogate":
    SPEC["states"]["gate"] = dict(SPEC["states"]["gate"], context="isolated")


def stub_llm(convo):
    return {"history_len": len(convo)}     # a plain dict IS the parsed value (v2)


flow = load_workflow(SPEC, {}, llm=stub_llm)
