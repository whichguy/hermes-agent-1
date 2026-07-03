"""wf_router — the independent edge judge (v2), all verdict paths, selected by $WF_ROUTER:

strict (default): routes are BINDING — the router's first verdict is `proceed` (illegal on a strict
step) -> router repair -> a declared label -> routes (skipping the fall-through neighbor).
optional: `"optional": true` legalizes `proceed` -> the step falls ONWARD in declaration order.
ask: the router cannot clearly route -> its reasoned question suspends the flow; the human's answer
is woven into the TASK convo; the task RE-ATTEMPTS; a fresh router round routes.
selfheal: an off-menu declared outcome is repaired into a valid one (fast path, no judge).
cantproceed: the router NEVER produces a valid verdict -> repairs exhaust -> the engine FORCES the
reasoned can't-proceed ask (the human informs; nobody hand-picks edges).
when_skips: a matching `when` rail routes for free — the router must never be called.
"""
import os

from workflow import load_workflow

CASE = os.environ.get("WF_ROUTER", "strict")

JUDGE = {"prompt": "Judge ${in}.", "routes": {"good": {"to": "fin", "means": "clearly acceptable"}}}
if CASE == "optional":
    JUDGE = dict(JUDGE, optional=True)
if CASE == "when_skips":
    JUDGE = dict(JUDGE, when=[{"if": {"path": "$.input.n", "eq": 9}, "to": "fin"}])

SPEC = {"id": "wf_router", "version": 1, "start": "judge", "states": {
    "judge": JUDGE,
    "mid": {"run": "mark"},                 # reached only via fall-through (optional proceed)
    "fin": {"run": "fin"},
}}


def stub_llm(convo):
    if CASE == "selfheal":
        # off-menu declared outcome -> the engine's _OUTCOME_REPAIR -> a valid one (no judge)
        if any('must include "outcome"' in (m.get("content") or "") for m in convo):
            return '{"claim": "original", "outcome": "good"}'
        return '{"claim": "original", "outcome": "banana"}'
    if any("The human answered:" in (m.get("content") or "") for m in convo):
        return '{"claim": "amended"}'
    return '{"claim": "original"}'


def stub_router(convo):
    if CASE == "when_skips":
        raise AssertionError("the when rail must skip the router entirely")
    if CASE == "optional":
        return '{"outcome": "proceed"}'
    if CASE == "ask":
        if "amended" in convo[-1]["content"]:
            return '{"outcome": "good"}'
        return '{"outcome": "ask", "question": "the claim lacks a budget figure - what budget applies?"}'
    if CASE == "cantproceed":
        return '{"verdict": "nonsense shape"}'
    # strict: an illegal `proceed` first (repaired), then a declared label
    if any("was invalid" in (m.get("content") or "") for m in convo):
        return '{"outcome": "good"}'
    return '{"outcome": "proceed"}'


def mark(flowing, state):
    return {"mid": True}


def fin(flowing, state):
    return {"done": True}


flow = load_workflow(SPEC, {"mark": mark, "fin": fin}, llm=stub_llm, router=stub_router)
