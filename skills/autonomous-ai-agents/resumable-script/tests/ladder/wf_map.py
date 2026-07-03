"""wf_map — the `map` kind: sequential map-reduce (inner `run` + reduce) then an inner `prompt` fan-out."""
from workflow import load_workflow

SPEC = {
    "id": "wf_map", "version": 1, "start": "fan",
    "states": {
        # inner `run` per item, reading the $.it / $.it_index bindings; reduce folds the per-item list.
        "fan": {"map": {"over": "$.input.items", "as": "it", "do": {"run": "summ"}},
                "reduce": {"run": "join"}, "intent": "summarise each item", "next": "fanp"},
        # inner `prompt` per item (any single kind); no reduce -> the list of per-item results flows.
        "fanp": {"map": {"over": "$.input.items", "as": "it", "do": {"prompt": "Summarize ${$.it.name}."}},
                 "intent": "prompt each item", "next": "@done"},
    },
}


def summ(flowing, state):
    # `flowing` is the item; state["it"] is the same item (as-binding); state["it_index"] the position.
    return {"label": state["it"]["name"].upper(), "idx": state["it_index"]}


def join(outs, state):
    # `outs` is the ordered list of per-item results.
    return {"labels": [o["label"] for o in outs], "idxs": [o["idx"] for o in outs]}


def stub_llm(convo):
    # The rendered prompt (with ${$.it.name} filled in) is the last user turn; echo it, no routing needed.
    return {"echo": convo[-1]["content"]}     # a plain dict IS the parsed value (v2)


flow = load_workflow(SPEC, {"summ": summ, "join": join}, llm=stub_llm)
