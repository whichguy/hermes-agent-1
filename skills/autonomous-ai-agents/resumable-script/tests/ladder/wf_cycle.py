"""wf_cycle — cycles are native (per-visit keys) and bounded by a fixed visit-cap safety constant.

Default spec: the report-revision loop — write -> review gate; "revise" routes to a free-text
feedback gate whose answer loops BACK to write; the write template reads ${$.feedback.decision}
(empty on lap 0, the human's text on lap 1 — missing -> ""). The stub llm echoes the rendered
prompt it saw, so the rung asserts the state threading through the loop directly, and an authored
`append` collects one entry per lap. WF_CYCLE=runaway selects a prompt<->prompt cycle whose stub
never stops — it must die at the fixed visit cap with a clear message, not spin forever.
"""
import os

from workflow import load_workflow

CASE = os.environ.get("WF_CYCLE", "revision")

if CASE == "runaway":
    SPEC = {
        "id": "wf_cycle", "version": 1, "start": "ping",
        "states": {
            "ping": {"prompt": "ping", "routes": {"again": "pong"}},
            "pong": {"prompt": "pong", "routes": {"again": "ping"}},
        },
    }

    def stub_llm(convo):
        return '{"spin": true, "outcome": "again"}'     # never converges -> max_visits must trip

    def stub_router(convo):
        raise AssertionError("fast path: the judge must never be consulted")
else:
    SPEC = {
        "id": "wf_cycle", "version": 1, "start": "write",
        "states": {
            "write": {"prompt": "Draft the report. Feedback so far: [${$.feedback.decision}]",
                      "append": {"$.laps": "${@.seen}"}},
            "review": {"ask": "Ship this draft?", "options": ["ship", "revise"],
                       "routes": {"ship": "@done", "revise": "feedback"}},
            "feedback": {"ask": "What should change?", "next": "write"},
        },
    }

    def stub_llm(convo):
        return {"seen": convo[-1]["content"]}           # echo the rendered prompt (isolated ctx)

    def stub_router(convo):
        raise AssertionError("no routed prompt step in the revision spec")


flow = load_workflow(SPEC, {}, llm=stub_llm, router=stub_router)
