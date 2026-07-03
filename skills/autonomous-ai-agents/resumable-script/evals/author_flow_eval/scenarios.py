"""scenarios.py — the SUITES, as pure data. Every scenario is executed by the ONE shared
`driver.run_scenario`; the suites are increasing-complexity groupings that all point at it.

Grading is BEHAVIORAL. Tasks describe the JOB (never option labels, state names, or step kinds — the
model authors its own graph); `evidence` checks what actually happened. The workhorse is the CANARY
pattern: distinctive values live only in the run's INPUT (the authoring model never sees them — tasks
must not quote them!), so a canary appearing in a rendered gate question proves the model authored a
`${...}` state reference and the engine filled it at runtime.

A scenario:
  id / suite     unique slug / pytest marker
  task           NL authoring prompt (job description only)
  input          JSON string with canary values
  intent         instruction for the LLM that answers each human gate
  expect         "completed" | "failed" — the behavioral routing verdict
  evidence       callable(ev) -> bool over
                 ev = {suspensions, answers, final, journal, prior_final}
  max_resumes    cycle budget (default 6)
  runs / input2  runs=2 reruns the SAME authored flow with input2(run-1 final state)
  attempts       author->run->answer tries before the scenario is judged failed
"""
import json


# ---- evidence helpers (shape-based, name-agnostic) -------------------------------------------
def gate_prompts(ev):
    return [(p.get("question") or {}).get("prompt") or "" for p in ev["suspensions"]]


def prompts_contain(ev, *needles):
    """Every needle appears in at least one rendered gate question (case-insensitive)."""
    blob = "\n".join(gate_prompts(ev)).lower()
    return all(str(n).lower() in blob for n in needles)


def _leaves(node, out):
    if isinstance(node, dict):
        for v in node.values():
            _leaves(v, out)
    elif isinstance(node, list):
        for v in node:
            _leaves(v, out)
    else:
        out.append(str(node).lower())


def state_has_leaf(ev, candidates):
    """Some leaf value in the final global state equals one of `candidates` (case-insensitive)."""
    state = (ev["final"].get("result") or {}).get("state") or {}
    out = []
    _leaves(state, out)
    return any(c.lower() in out for c in candidates)


def looped(ev):
    """The journal shows a state re-visit: workflow step keys are `<state>#<visit>` and replay never
    re-appends started records, so any visit >= 1 means a route genuinely cycled back."""
    for r in ev["journal"]:
        if r.get("type") == "step_started":
            head = (r.get("key") or "").split("/")[0]
            if "#" in head:
                try:
                    if int(head.rsplit("#", 1)[1]) >= 1:
                        return True
                except ValueError:
                    pass
    return False


def completed(ev):
    return True                                        # terminal verdict already checked by the driver


# ---- L1 — spine + interpolation: input values must surface in the human's question ----------
L1 = [
    {
        "id": "l1_expense", "suite": "L1",
        "task": ("An expense-approval workflow. Look at the incoming request, then pause and ask a "
                 "human reviewer whether to approve it — the question shown to the reviewer must "
                 "include the request's concrete details (who it is from, and the amount) so the "
                 "reviewer can decide without digging. If approved, finish successfully; if not, fail."),
        "input": '{"customer": "ACME-9931", "amount": 4172}',
        "intent": "Approve the request.",
        "expect": "completed",
        "evidence": lambda ev: prompts_contain(ev, "ACME-9931", "4172"),
        "attempts": 2,
    },
]

# ---- L2 — prior-step threading: a MODEL step's stored finding surfaces in a later gate -------
L2 = [
    {
        "id": "l2_triage", "suite": "L2",
        "task": ("A triage workflow. First an LLM step reads the incoming request and stores a "
                 "priority for it in workflow state — exactly one word: high or low. Then a human gate "
                 "asks whether to accept the triage, and its question must show BOTH the stored "
                 "priority AND the request's reference code. Accepting finishes the workflow; "
                 "declining fails it."),
        "input": '{"reference": "TCK-77419", "description": "server room is on fire"}',
        "intent": "Accept the triage.",
        "expect": "completed",
        # the priority is model-chosen but constrained to {high, low}: seeing it in the rendered gate
        # question proves a later template referenced state written by an earlier model step
        "evidence": lambda ev: prompts_contain(ev, "TCK-77419")
                               and any(w in "\n".join(gate_prompts(ev)).lower()
                                       for w in ("high", "low")),
        "attempts": 3,
    },
]

# ---- L3 — runtime routing (the digraph test): same task, two inputs, two verdicts ------------
_L3_TASK = ("A validation workflow. An LLM step examines the incoming request and decides whether it "
            "is well-formed: it must have a non-empty customer name and a positive numeric amount. "
            "If well-formed, continue to a human confirmation gate and finish successfully after "
            "confirmation. If malformed, the workflow must fail without asking anyone.")
L3 = [
    {
        "id": "l3_valid", "suite": "L3",
        "task": _L3_TASK,
        "input": '{"customer": "ACME-9931", "amount": 250}',
        "intent": "Confirm / approve the request.",
        "expect": "completed",
        "evidence": completed,
        "attempts": 3,
    },
    {
        "id": "l3_invalid", "suite": "L3",
        "task": _L3_TASK,
        "input": '{"customer": "", "amount": -5}',
        "intent": "Confirm / approve the request.",   # should never be consulted
        "expect": "failed",
        "evidence": completed,
        "attempts": 3,
    },
]

# ---- L4 — cycles: a gate routes back until the reviewer is satisfied -------------------------
L4 = [
    {
        "id": "l4_revision", "suite": "L4",
        "task": ("A draft-review loop. Prepare a draft, then ask a human reviewer whether the draft "
                 "is acceptable or needs another revision pass. If a revision is requested, go back, "
                 "redo the draft, and ask again — as many times as needed. When accepted, finish "
                 "successfully."),
        "input": '{"document": "Q3 report"}',
        "intent": "The FIRST time you are asked, request a revision. After that, accept/approve.",
        "expect": "completed",
        "evidence": lambda ev: len(ev["suspensions"]) >= 2 and looped(ev),
        "max_resumes": 6,
        "attempts": 3,
    },
]

# ---- L5 — cross-run state: run 2's question references run 1's outcome -----------------------
L5 = [
    {
        "id": "l5_followup", "suite": "L5",
        "task": ("A review workflow that is aware of its own history. The input holds the current "
                 "request (an 'item' field), and MAY hold the complete final state of a previous run "
                 "under 'previous' (the previous run's original request sits at previous.input). Ask a "
                 "human reviewer to approve the current item; when a previous outcome is present, the "
                 "question must also mention which item was handled last time. Approve to finish, "
                 "otherwise fail."),
        "input": '{"item": "PRIOR-88113"}',
        "intent": "Approve the request.",
        "expect": "completed",
        "runs": 2,
        "input2": lambda state1: json.dumps({"item": "NEW-22904", "previous": state1}),
        # graded against run 2: the current canary AND the run-1 canary (reachable only through the
        # previous run's embedded final state) must both surface in a rendered question
        "evidence": lambda ev: prompts_contain(ev, "NEW-22904", "PRIOR-88113"),
        "attempts": 3,
    },
]

# ---- L6 — flagship: agent reads state via tools, writes a finding, gate surfaces both --------
L6 = [
    {
        "id": "l6_risk", "suite": "L6",
        "task": ("A risk-triage workflow. An AGENT step must use its get_state tool to read the "
                 "incoming request from workflow state, judge its risk, and use set_state to record "
                 "the risk in state as exactly one word: low, medium, or high. Then a human gate asks "
                 "whether to accept — its question must include the recorded risk level and the "
                 "customer name. Accept to finish successfully, decline to fail."),
        "input": '{"customer": "ACME-9931", "amount": 98765}',
        "intent": "Accept.",
        "expect": "completed",
        "evidence": lambda ev: prompts_contain(ev, "ACME-9931")
                               and state_has_leaf(ev, ["low", "medium", "high"]),
        "attempts": 3,
    },
]


SCENARIOS = {
    "L1": L1,
    "L2": L2,
    "L3": L3,
    "L4": L4,
    "L5": L5,
    "L6": L6,
}
