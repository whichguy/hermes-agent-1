"""wf_mapedge — map edge cases: an EMPTY `over` list (reduce still runs, no phantom iteration), a
custom `as` name, and a NESTED `over` path with the $.<as>_index binding."""
from workflow import load_workflow

SPEC = {
    "id": "wf_mapedge", "version": 1, "start": "empty",
    "states": {
        # over [] -> zero item steps, but reduce([]) still folds -> {"n": 0}.
        "empty": {"map": {"over": "$.input.none", "as": "it", "do": {"run": "box"}},
                  "reduce": {"run": "count"}, "next": "nested"},
        # nested `over` path + custom `as` name + $.<as>_index; no reduce -> the list flows.
        "nested": {"map": {"over": "$.input.data.items", "as": "row", "do": {"run": "rowlabel"}},
                   "next": "@done"},
    },
}


def box(item, state):
    return {"v": item}


def count(outs, state):
    return {"n": len(outs)}


def rowlabel(item, state):
    return {"who": state["row"]["id"], "i": state["row_index"]}


flow = load_workflow(SPEC, {"box": box, "count": count, "rowlabel": rowlabel})
