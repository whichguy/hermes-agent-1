"""cheatsheet.py — the authoring instructions handed to the real LLM (v2 format).

This is the ONE place the workflow format is taught to the model that will author a spec. v2 is much
simpler to teach than the old envelope format: directives just describe the job (the ENGINE owns the
return contract, the ASK: interrupt convention, and routing judgments — a separate router model picks
edges against the `means` conditions), states fall through sequentially when unrouted, and state
arrives in prompts via ${...} holes.

`REGISTRY` names below are the FIXED set of run functions the harness provides at wrap time
(`wrapper.REGISTRY`). The model may reference ONLY these by name in a `run` state.
"""

# The run functions the harness will provide. Keep in lockstep with wrapper.REGISTRY.
REGISTRY_DOC = """\
Available `run` functions (each takes (flowing_input, state) and returns a small JSON object) — you may
reference ONLY these names in a `run` state, nothing else:
  - "begin"  : marks the start; returns {"ok": true, "input": <flowing>}
  - "record" : returns {"recorded": <flowing>}
  - "finish" : marks the end; returns {"done": true}
"""

FORMAT = """\
You author a WORKFLOW SPEC: a small JSON state machine (a directed graph — cycles are allowed).

SHAPE
{
  "id": "<slug>", "version": 1, "start": "<state name>",
  "states": { "<name>": { <one kind> , <optional routing/set> }, ... }
}
Each state has EXACTLY ONE kind. "@done" and "@fail" are the terminals (never define them as states);
"@done" completes the workflow, "@fail" fails it.

KINDS
  "run":    "<registry-fn-name>"   -> a plain function, no LLM.
  "prompt": "<instruction text>"   -> one LLM call. Write the instruction like a good task brief; you
                                      may tell it what JSON shape to reply with (e.g. 'Reply as JSON
                                      {"category": ..., "severity": 1-5}'). If you don't, it replies
                                      {"result": <its output>}. The step's parsed reply is stored in
                                      state under the state's name.
  "agent":  "<instruction text>"   -> a full agent turn with tools (get_state / set_state to read and
                                      write workflow state live).
  "ask":    "<question text>"      -> ask the HUMAN. The workflow pauses until they answer; the answer
                                      is stored as {"decision": <answer>}. Give it "options" (a list
                                      of short labels) when the answer should be one of a few choices.

ROUTING (the graph edges)
  - DEFAULT: no routing keys at all -> the workflow simply proceeds to the NEXT state you declared;
    the LAST declared state finishes at @done. Linear flows need zero routing.
  - "next": "<state-or-@terminal>" -> an explicit unconditional edge (jumps and loops back are fine).
  - "routes": {"<label>": "<target>", ...} on a prompt/agent/ask state -> a decision among edges.
    For prompt/agent, the step's reply DECLARES its exit: include "outcome": "<label>" in the JSON
    (the engine states the exact menu before your instruction at runtime, and an independent judge
    is the fallback if the field is missing) — give each label a condition with the object form:
      "routes": {"valid":   {"to": "payout",  "means": "the claim fully satisfies policy"},
                 "suspect": {"to": "manager", "means": "policy unmet or possible fraud"}}
    Routes are BINDING (the judge must pick one). Add "optional": true on the state to allow
    "none of these applies -> just proceed to the next state".
    For "ask", the human's chosen option IS the label; map every option in routes.
  - "when": [{"if": {"path": "$.amount", "gt": 1000}, "to": "review"}, ...] -> MECHANICAL conditions
    checked first, for free (operators: eq/ne/gt/gte/lt/lte, or a bare "$.path" for truthiness).
    Use `when` whenever the condition is a value/range test — never spend a model call on arithmetic.

CONVERSATION
  A workflow's prompt/agent steps share ONE running conversation by default (later steps see earlier
  exchanges), and ask-gate questions + the human's answers join it as turns. Add "context":
  "isolated" on a step (or at the top of the spec) for steps that should see only their own
  instruction. Do NOT add "next" to a state that has routes (routes are binding; that next would be
  dead) unless you also set "optional": true. Even inside the shared conversation, use ${...} holes to put the
  CONCRETE data (names, amounts, findings) directly in the instruction — explicit beats implied.

STATE + INTERPOLATION (how data flows)
  Global state is one JSON object. It starts as {"input": <the run's input>} and every state's parsed
  result is auto-stored under the state's name. Any instruction/question text may embed live values
  with ${...} holes, rendered when the state runs:
    ${$.input.customer}      -> a field of the run's input
    ${$.assess.risk}         -> a field of an earlier state's stored reply
    ${in}                    -> the previous state's result
  Example: "ask": "Approve the ${$.input.kind} request from ${$.input.customer}?"
  GOOD flows surface the CONCRETE data (names, amounts, findings) in their questions and instructions
  instead of asking generic questions — use ${...} holes for that.
  To promote a field of a step's reply to a top-level state key, add on that state:
    "set": {"$.priority": "${@.priority}"}      (@ = this state's parsed reply)
  CAUTION: `$${` is the ESCAPE for a literal `${` — never write a dollar sign directly before a hole
  ("$${$.amount}" comes out as dead literal text). Write "${$.amount} USD" or "${$.amount} dollars".

INTERRUPTIONS (automatic — do not write instructions for this)
  Any prompt/agent step can pause the workflow to ask the human for missing information, and the
  judge can pause it when an output cannot be clearly routed. The engine teaches the model how; you
  just write the task.

LOOPS
  Route back to an earlier state to revise/retry (each pass re-runs it). Loops are bounded (25 visits
  per state by default; set a spec-level "max_visits" to change it). Make sure every cycle can exit —
  a human gate, a `when` condition, or a judged route that eventually leaves the loop.

CONSTRAINTS
  - Reference ONLY the run functions listed below.
  - Keep it small: the fewest states that genuinely do the job.
  - The declared "start" MUST be one of your state names.

""" + REGISTRY_DOC + """
OUTPUT: reply with ONE JSON object — the spec — and NOTHING else. No prose, no code fences, no
explanation. Just the JSON.
"""

SYSTEM = ("You are a precise workflow-spec author. You output only a single JSON workflow spec object, "
          "strictly following the format you are given. No prose, no markdown fences.")


def author_prompt(task, error=None):
    """Build the user prompt for one authoring turn. On a repair round, `error` is the exact validation
    failure from the previous attempt so the model can correct it."""
    parts = [FORMAT, "TASK:\n" + task.strip()]
    if error:
        parts.append(
            "Your previous reply was REJECTED with this error:\n  %s\nFix it and output the corrected "
            "JSON spec only." % error)
    return "\n\n".join(parts)
