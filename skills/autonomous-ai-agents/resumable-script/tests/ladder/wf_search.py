"""wf_search — the `search` kind: injected caller returns structured results, routable via $.<step>.results[..]."""
from workflow import load_workflow

SPEC = {
    "id": "wf_search", "version": 1, "start": "research",
    "states": {
        # query interpolates ${$.input.topic}; result auto-stores at $.research; route on the top URL.
        "research": {"search": "${$.input.topic} latest", "format": "structured",
                     "intent": "research the topic",
                     "when": [{"if": {"path": "$.research.results[0].url",
                                      "eq": "https://ex.com/refunds"}, "to": "found"}],
                     "next": "missing"},
        "found": {"run": "pick", "next": "@done"},
        "missing": {"run": "nourl", "next": "@fail"},
    },
}


def stub_search(query, fmt):
    # Deterministic offline web caller. Echoes the query so the test can assert interpolation + format.
    return {"query": query, "format": fmt, "results": [
        {"title": "Refund policy", "url": "https://ex.com/refunds", "snippet": "how refunds work"},
        {"title": "Other", "url": "https://ex.com/other", "snippet": "..."},
    ]}


def pick(flowing, state):
    return {"top": state["research"]["results"][0]["url"], "q": state["research"]["query"],
            "fmt": state["research"]["format"]}


def nourl(flowing, state):
    return {"top": None}


flow = load_workflow(SPEC, {"pick": pick, "nourl": nourl}, search=stub_search)
