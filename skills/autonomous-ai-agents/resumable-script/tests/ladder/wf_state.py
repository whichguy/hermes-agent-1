"""wf_state — state threading: flowing pipe + named global (set/append) + auto-store under $.<id>."""
from workflow import load_workflow

SPEC = {
    "id": "wf_state", "version": 1, "start": "a",
    "states": {
        "a": {"run": "mk", "intent": "make a value",
              "set": {"$.kept": "${@.val}"}, "append": {"$.audit": "${@.val}"}, "next": "b"},
        "b": {"run": "use", "intent": "consume it", "next": "@done"},
    },
}


def mk(flowing, state):
    return {"val": 7, "extra": 1}


def use(flowing, state):
    # flowing is step a's result (the pipe); state.kept came from the `set` mutation.
    return {"saw_kept": state.get("kept"), "saw_flow": flowing.get("val"),
            "saw_autostore": state.get("a", {}).get("extra")}


REGISTRY = {"mk": mk, "use": use}
flow = load_workflow(SPEC, REGISTRY)
