"""wf_onerr — declarative failure routing (`on_error` matcher rules), selected by $WF_ONERR:
route (list form: retry then reroute), routeobj (object sugar), retryonly (retries then exit 1),
match (per-error-class ladder + `when` reading the sentinel), fallback (deep `result`
substitution feeding a map), replay (failure branch memoized across resume). The fetch step
fails iff $BREAK=1; $FAIL_KIND picks the error class (value|timeout)."""
import os

from workflow import load_workflow


def fetch(flowing, state):
    if os.environ.get("BREAK") == "1":
        kind = os.environ.get("FAIL_KIND")
        if kind == "timeout":
            raise TimeoutError("upstream timed out")
        if kind == "timeoutnl":
            raise TimeoutError("connection timeout\n")   # trailing \n: the $-anchor parity case
        raise ValueError("bad payload")
    return {"items": [1, 2]}


def cleanup(flowing, state):
    # the failure branch: flowing is the sentinel (no `result` rule), so surface its message
    return {"cleaned": True, "why": (flowing.get("__error__") or {}).get("message", "")}


def box(item, state):
    return item * 10


REG = {"fetch": fetch, "cleanup": cleanup, "box": box}
CASE = os.environ.get("WF_ONERR", "route")

if CASE == "routeobj":
    ON_ERROR = {"retries": 1, "to": "cleanup"}                    # object sugar == one-rule list
elif CASE == "retryonly":
    ON_ERROR = [{"retries": 2}]                                   # try harder, then today's exit 1
elif CASE == "match":
    ON_ERROR = [{"match": "Timeout", "retries": 2},               # per-class: retry timeouts...
                {"match": "*", "to": "cleanup"}]                  # ...reroute everything else
elif CASE == "matchnl":
    ON_ERROR = [{"match": "timeout$", "to": "cleanup"}]   # must match a message ending in \n in BOTH engines
elif CASE == "fallback":
    ON_ERROR = [{"result": {"items": [], "why": "${@.__error__.message}"}}]
elif CASE == "replay":
    ON_ERROR = [{"to": "confirm"}]
else:  # route
    ON_ERROR = [{"retries": 1, "to": "cleanup"}]

CLEANUP = {"run": "cleanup", "next": "@done"}
if CASE == "match":
    # prove `when` predicates can read the memoized sentinel: ValueError -> @done, else @fail
    CLEANUP = {"run": "cleanup",
               "when": [{"if": {"path": "$.fetch.__error__.name", "eq": "ValueError"}, "to": "@done"}],
               "next": "@fail"}

SPEC = {"id": "wf_onerr", "version": 1, "start": "fetch", "states": {
    "fetch": {"run": "fetch", "on_error": ON_ERROR, "next": "use"},
    "use": {"map": {"over": "$.fetch.items", "do": {"run": "box"}}, "next": "@done"},
    "cleanup": CLEANUP,
    "confirm": {"ask": "Fetch failed: ${$.fetch.__error__.message}. Continue?",
                "options": ["ok"], "next": "@done"},
}}
flow = load_workflow(SPEC, REG)
