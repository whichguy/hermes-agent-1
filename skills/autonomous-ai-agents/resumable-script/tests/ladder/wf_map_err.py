"""wf_map_err — map per-item failure policy (`on_item_error`), selected by $WF_MAPERR:
collect (sentinel keeps its position; reduce sees it), skip (positions compress), fail
(default: one item throw kills the flow), retry (map-level retries apply per item), gate
(collect + a decide gate after the map — the replay rung resumes with $BREAK unset and the
memoized per-item sentinel must hold). The `probe` fn throws on item "bad" iff $BREAK=1."""
import os

from workflow import load_workflow


def probe(item, state):
    if item == "bad" and os.environ.get("BREAK") == "1":
        raise ValueError("probe failed: bad")
    return "ok:" + item


def tally(outs, state):
    return {"n": len(outs), "outs": outs}


REG = {"probe": probe, "tally": tally}
CASE = os.environ.get("WF_MAPERR", "collect")

MAP = {"over": "$.input.items", "do": {"run": "probe"}}
if CASE == "skip":
    MAP["on_item_error"] = "skip"
elif CASE == "retry":
    MAP["on_item_error"] = "collect"
    MAP["retries"] = 1
elif CASE in ("collect", "gate"):
    MAP["on_item_error"] = "collect"
# CASE == "fail": no policy -> today's behavior (one throw kills the flow)

STATES = {"scan": {"map": MAP, "reduce": {"run": "tally"}, "next": "@done"}}
if CASE == "gate":
    STATES["scan"] = {"map": MAP, "reduce": {"run": "tally"}, "next": "confirm"}
    STATES["confirm"] = {"ask": "Scan hit ${$.scan.n} items. Continue?",
                         "options": ["ok"], "next": "@done"}

SPEC = {"id": "wf_map_err", "version": 1, "start": "scan", "states": STATES}
flow = load_workflow(SPEC, REG)
