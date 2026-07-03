"""call_wf_vetting — a WORKFLOW-SPEC flow used as a ctx.call CHILD (see call_wf_child.py).

The child exercises every state-retention surface across nested suspensions: `prep` computes a
token BEFORE any pause; `assess` is an ASK-interrupting prompt whose post-answer reply ECHOES its
conversation shape (pinning the no-reissue continuation contract inside the embedded journal);
`review` is an ask gate whose template renders the pre-pause token (state through pause #1); `fin`
reads both the token and the authored-set verdict (state through pause #2). Kept in its own module
so load_flow's first-flow-attribute scan in the parent file finds the PARENT, not this child."""
import json

from workflow import load_workflow

CHILD_SPEC = {
    "id": "vetting", "version": 1, "start": "prep",
    "states": {
        "prep": {"run": "prep"},                                    # falls to assess
        "assess": {"prompt": "Assess ${in} holding ${$.prep.token}.",
                   "set": {"$.verdict": "${@.verdict}"},
                   "routes": {"good": "review"}},
        "review": {"ask": "Confirm ${$.prep.token}?", "options": ["yes", "no"],
                   "routes": {"yes": "fin", "no": "@fail"}},
        "fin": {"run": "fin"},                                      # last -> @done
    },
}


def prep(flowing, state):
    return {"token": "T-909"}


def fin(flowing, state):
    return {"done": True, "token_seen": state["prep"]["token"], "verdict": state.get("verdict")}


def stub_llm(convo):
    if any("The human answered:" in (m.get("content") or "") for m in convo):
        return json.dumps({"verdict": "good", "outcome": "good",
                           "echo_roles": [m["role"] for m in convo],
                           "echo_last": convo[-1]["content"],
                           "echo_prev": convo[-2]["content"]})
    return "ASK: need clearance level"


def stub_router(convo):
    return '{"outcome": "good"}'


child = load_workflow(CHILD_SPEC, {"prep": prep, "fin": fin}, llm=stub_llm, router=stub_router)
