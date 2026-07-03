"""wf_specbad — spec-hardening rejections happen at LOAD (usage exit, no journal), selected by
$WF_SPECBAD: inner_routes, no_llm, bad_format, as_input, onerr_prompt, onerr_badto, onerr_empty,
onerr_badregex, onerr_inlineflags, inner_onerr, exh_run, idem_search, nested_map, reduce_kind,
reduce_routes, bad_namespace, bad_maxvisits, pred_twoops, pred_badop, ask_unmapped, dead_next,
bad_spec_context, flow_kind, agent_kind."""
import os

from workflow import load_workflow


def box(item, state):
    return item


REG = {"box": box}
CASE = os.environ.get("WF_SPECBAD", "inner_routes")

if CASE == "no_llm":
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"prompt": "hi", "next": "@done"}}}
    flow = load_workflow(SPEC, REG)
elif CASE == "bad_format":
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"search": "q", "format": "xml", "next": "@done"}}}
    flow = load_workflow(SPEC, REG, search=lambda q, f: {})   # caller present -> the FORMAT is what fails
elif CASE == "as_input":
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"map": {"over": "$.input.xs", "as": "input", "do": {"run": "box"}},
                             "next": "@done"}}}
    flow = load_workflow(SPEC, REG)
elif CASE == "onerr_prompt":
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"prompt": "hi", "on_error": [{"to": "@done"}], "next": "@done"}}}
    flow = load_workflow(SPEC, REG)
elif CASE == "onerr_badto":
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"run": "box", "on_error": [{"to": "nope"}], "next": "@done"}}}
    flow = load_workflow(SPEC, REG)
elif CASE == "onerr_empty":
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"run": "box", "on_error": [], "next": "@done"}}}
    flow = load_workflow(SPEC, REG)
elif CASE == "onerr_badregex":
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"run": "box", "on_error": [{"match": "([", "to": "@done"}], "next": "@done"}}}
    flow = load_workflow(SPEC, REG)
elif CASE == "onerr_inlineflags":
    # (?i) compiles in Python but not JS — the portability gate must reject it in BOTH engines
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"run": "box", "on_error": [{"match": "(?i)timeout", "to": "@done"}], "next": "@done"}}}
    flow = load_workflow(SPEC, REG)
elif CASE == "inner_onerr":
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"map": {"over": "$.input.xs",
                                     "do": {"run": "box", "on_error": [{"to": "@done"}]}},
                             "next": "@done"}}}
    flow = load_workflow(SPEC, REG)
elif CASE == "exh_run":
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"run": "box", "on_exhausted": {"to": "@done"}, "next": "@done"}}}
    flow = load_workflow(SPEC, REG)
elif CASE == "idem_search":
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"search": "q", "idempotent": False, "next": "@done"}}}
    flow = load_workflow(SPEC, REG, search=lambda q, f: {})
elif CASE == "nested_map":
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"map": {"over": "$.input.xs",
                                     "do": {"map": {"over": "$.input.ys", "do": {"run": "box"}}}},
                             "next": "@done"}}}
    flow = load_workflow(SPEC, REG)
elif CASE == "reduce_kind":
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"map": {"over": "$.input.xs", "do": {"run": "box"}},
                             "reduce": {"ask": "not a run step"},
                             "next": "@done"}}}
    flow = load_workflow(SPEC, REG)
elif CASE == "reduce_routes":
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"map": {"over": "$.input.xs", "do": {"run": "box"}},
                             "reduce": {"run": "box", "routes": {"a": "@done"}},
                             "next": "@done"}}}
    flow = load_workflow(SPEC, REG)
elif CASE == "bad_namespace":
    SPEC = {"id": "wf_specbad", "version": 1, "namespace": "Billing Ops!", "start": "s",
            "states": {"s": {"run": "box", "next": "@done"}}}
    flow = load_workflow(SPEC, REG)
elif CASE == "bad_maxvisits":
    SPEC = {"id": "wf_specbad", "version": 1, "max_visits": 0, "start": "s",
            "states": {"s": {"run": "box", "next": "@done"}}}
    flow = load_workflow(SPEC, REG)
elif CASE == "pred_twoops":
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"run": "box",
                             "when": [{"if": {"path": "$.x", "eq": 1, "ne": 9}, "to": "@done"}],
                             "next": "@done"}}}
    flow = load_workflow(SPEC, REG)
elif CASE == "pred_badop":
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"run": "box",
                             "when": [{"if": {"path": "$.x", "gte ": 1}, "to": "@done"}],
                             "next": "@done"}}}
    flow = load_workflow(SPEC, REG)
elif CASE == "ask_unmapped":
    # routes are binding: an `ask` option a human can pick must map somewhere (or "optional": true)
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"ask": "Pick.", "options": ["a", "b"], "routes": {"a": "@done"}}}}
    flow = load_workflow(SPEC, REG)
elif CASE == "dead_next":
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"prompt": "hi", "routes": {"a": "@done"}, "next": "@done"}}}
    flow = load_workflow(SPEC, REG, llm=lambda c: "{}")
elif CASE == "bad_spec_context":
    SPEC = {"id": "wf_specbad", "version": 1, "context": "sidebar", "start": "s",
            "states": {"s": {"run": "box", "next": "@done"}}}
    flow = load_workflow(SPEC, REG)
elif CASE == "flow_kind":
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"flow": "child"}}}
    flow = load_workflow(SPEC, REG)
elif CASE == "agent_kind":
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"agent": "do it"}}}
    flow = load_workflow(SPEC, REG)
else:  # inner_routes — the previously-harmful case: an inner prompt/run carrying routing keys
    SPEC = {"id": "wf_specbad", "version": 1, "start": "s",
            "states": {"s": {"map": {"over": "$.input.xs",
                                     "do": {"run": "box", "routes": {"a": "@done"}}},
                             "next": "@done"}}}
    flow = load_workflow(SPEC, REG)
