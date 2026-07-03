# Authoring workflows

A workflow is a small JSON state machine — a **directed graph, cycles welcome** — run durably on the
resumable-script engine. Authors (human or LLM) write *prompts + edges + a function registry*; the
engine owns control flow, model contracts, routing judgments, interruption, and resume.

**The mental model in three sentences:**
1. **State is one growing JSON document** — each step's output lands under its own name; templates
   read slices of it via `${...}` holes. That is the entire state-passing story.
2. **Each hop is render → call → store → route** — every LLM call is small and blind; the ENGINE is
   the orchestrator; no prompt ever manages the workflow.
3. **Routing is decided by whoever is qualified**: a `when` predicate if it's mechanical, the router
   (an independent judge model) if it's judgment, the human if it's consequential, and file order if
   nobody says otherwise.

## 1. Shape of a spec — the canonical example

```json
{ "id": "complaint", "version": 1, "start": "classify",
  "states": {
    "classify": { "prompt": "Classify this complaint. Reply as JSON {\"category\": ..., \"severity\": 1-5}:\n${in}" },
    "research": { "prompt": "Our policy areas: refunds, replacements, apologies-only. Summarize in 3 bullets what policy allows for a '${$.classify.category}' complaint." },
    "assess":   { "prompt": "Complaint: ${$.input.text}\nPolicy: ${$.research.result}\nSeverity: ${$.classify.severity}\nAssess what we should offer and state your evidence.",
                  "routes": {
                    "give_refund": {"to": "draft",   "means": "policy clearly allows a refund here"},
                    "escalate":    {"to": "manager", "means": "high severity or policy is ambiguous"} }},
    "manager":  { "ask": "Sev-${$.classify.severity} complaint, assessment: ${$.assess.recommend}. Refund anyway?",
                  "options": ["refund", "reject"],
                  "routes": {"refund": "draft", "reject": "@fail"} },
    "draft":    { "prompt": "Write a short reply granting the refund. Complaint: ${$.input.text}. Assessment: ${$.assess.recommend}" },
    "send":     { "run": "send_email" }
  }
}
```

Each state has **exactly one kind**, optional routing, and an optional `intent` (narration + journaled
`desc`). `@done` / `@fail` are the terminals (`@fail` fails the flow). Note how little routing exists:
`classify → research → assess` and `draft → send → @done` are pure **sequential fall-through** —
unrouted states proceed in declaration order and the last declared state finishes at `@done`.

Two optional spec-level keys: `"namespace": "billing"` (segments of `[a-z0-9_-]+` split by `/` or `.`)
folds into the flow identity — the journal's `flow_id` becomes `billing/complaint`, so "everything
under billing/" is a prefix match anywhere runs are listed; `"max_visits": N` (default 25) bounds
per-state revisits (see §8b Cycles).

**Trace A (clean run** — "blender arrived cracked, order #4417"**):** `classify#0/llm#0` replies JSON
→ stored at `$.classify` → falls through. `research` renders `${$.classify.category}` (reach-back into
hop 1) → falls through. `assess` = **two calls**: the task (`assess#0/llm#0`) states its evidence,
then the independent router (`assess#0/route#0`) judges `{"outcome": "give_refund"}` → jumps to
`draft`, **skipping `manager`** (edges beat file order). `draft` reads `${$.input.text}` +
`${$.assess.recommend}` (any step reads any earlier step) → falls to `send` (a real side effect,
exactly-once) → last state → `@done`. Five model calls (4 task + 1 router), zero interruptions.

**Trace B (the interrupt** — "my thing broke, want money back"**):** hops 1–2 identical;
`assess#0/llm#0` replies `ASK: Which product, what order number, when did it arrive?` → **no router
call** — a durable gate `assess#0/intervene#0` opens and the process **exits 10**. Nothing runs while
the human thinks. Days later, `resume --answer '"Blender, #4417, last Tuesday"'` → replay restores
hops 1–3a with **zero model calls**, the answer is woven into the task conversation, the task
re-attempts (`assess#0/llm#1`), the router routes, hops 4–5 finish. The two-day pause is just one
step taking a long time.

Runnable with deterministic stubs: `examples/complaint.py` (+ `complaint.workflow.json`) drives both
traces offline.

## 2. Running a workflow

Pair the spec with a **registry** of plain functions and the model callers, expose a `flow`, drive it
through the engine CLI:

```python
# complaint.py
from workflow import load_workflow_file

def send_email(flowing, state):              # run fns are (flowing_input, state_snapshot) -> result
    return {"sent": True}

flow = load_workflow_file("complaint.workflow.json", {"send_email": send_email},
                          llm=my_llm, router=my_router)     # router defaults to llm if omitted
```

```bash
python3 scripts/engine.py run    --flow complaint.py --input '{"text":"..."}' --state-dir ./run1
python3 scripts/engine.py resume --flow complaint.py --answer '"refund"'      --state-dir ./run1
```

Callers are `llm(convo) -> str` and `router(convo) -> str` — `convo` is a list of `{role, content}`
messages (the leading message is the engine's `system` scaffold) and the return is the model's RAW
text (the engine owns parsing, repair, and routing). `agent` callers additionally receive
`(convo, state_snapshot, state_dir)` and may return `{"text": ..., "set": {...}}` (§11). A caller
returning a plain dict is treated as the already-parsed value — handy for deterministic test stubs.

## 3. Step kinds

| kind | what it is | who produces the value | routing source |
|---|---|---|---|
| `run` | a registry function `(flowing, state) -> result`. No LLM. | code | `when` / `next` / fall-through |
| `prompt` | ONE model call: the author's directive, verbatim. | the model | the ROUTER (when `routes` exist) |
| `agent` | a full Hermes agent (`hermes -z`), tools + live `get_state`/`set_state` (§11). | the model + tools | the ROUTER |
| `ask` | ask the HUMAN — the mirror of `prompt` with a person in the model's seat. Suspends until answered; the answer is the step's value (`{"decision": ...}`). | the human | the answer IS the pick (via `routes`) |
| `search` | one memoized call to an injected web caller (§3a). | code | `when` / `next` / fall-through |
| `map` | sequential map-reduce over a `$`-path list (§3b). | per-item steps | `when` / `next` / fall-through |
| `flow` | a durable CHILD workflow, invoked by name (§3c). | the child workflow | `when` / `next` / fall-through |

`ask` (renamed from `decide`) is **human input**, not just routing: with no `options`/`routes` it is a
free-text question whose answer feeds later templates (`${$.feedback.decision}`) — the same skeleton
as `prompt`, different oracle.

### 3a. `search` — injected web lookup

```json
"research": {"search": "${$.input.topic} refund policy", "format": "structured",
             "set": {"$.top_url": "${$.research.results[0].url}"}}
```

The query template renders from state, then the caller you inject as `load_workflow(..., search=fn)`
runs once, memoized. Like `run`, it returns **data, not routing** — route with `when`/`next`/
fall-through and read `$.<step>…`. `format` ∈ `structured` (default: `{"results": [{title,url,
snippet}, …]}`) · `html` · `both` — validated at load. A spec using `search`/`prompt`/`agent` with no
injected caller fails **at load**.

### 3b. `map` — sequential map-reduce

```json
"summarise": {"map": {"over": "$.input.items", "as": "it", "do": {"run": "summarise_item"}},
              "reduce": {"run": "tally"}}
```

- `over` must resolve to a **list**; each item runs the inner `do` stepdef (any single kind except
  `map`) with the item as the flowing input and `$.<as>` / `$.<as>_index` bound; each item is a
  durable sub-step `<step>/map#<i>` (replay reuses answered items).
- Results collect into a list; `reduce` (a `run`) folds them, else the list flows.
- **Map is a pure fan-out, enforced at load**: `do`/`reduce` cannot carry routing or mutation keys,
  and inner prompts never consult the router. Classify per item INTO the results, then route on the
  aggregate after the map. Per-item failure policy: `on_item_error` fail/skip/collect + retries (§8a).

### 3c. `flow` — a child workflow (data-defined nesting)

```json
"vet": { "flow": "vetting", "input": "${$.intake.item}", "intent": "vet the request" }
```

- The name resolves in `load_workflow(..., flows={name: <spec dict> | <Flow>})` (an inline child
  spec is just a dict value; unknown names fail at load). Child specs are validated eagerly and
  compiled lazily (a self-/mutually-recursive registry loads fine); children inherit the parent's
  callers (`llm`/`router`/`search` — the `agent` kind is barred inside children, clear error).
- `input` (optional, `${...}`-resolved) is the child's run input; default = the flowing value.
- The child runs durably via the engine's nested-call machinery: its suspensions HOIST to the parent
  with composed keys — `pending.key = "vet#0/gate#0"`, `chain = ["vet#0", "gate#0"]` — and the key
  round-trips verbatim through `resume` (any depth). The child's whole journal is embedded in ONE
  parent record while parked (see §10a and `references/nested-flows.md`).
- On completion, the child's `{"result": ..., "state": ...}` auto-stores at `$.<state>`:
  `${$.vet.result}` and `${$.vet.state.<child-stage>}` are addressable from any later parent state —
  child stage outputs and child global state stay fully transparent.
- A DATA step: no router; route with `when`/`next`/fall-through. Each visit = a fresh child run
  (loop-friendly, bounded by the parent's `max_visits`). Not allowed as a map `do`.
- **Caveat (exactly-once):** children are memory-backed between suspensions — never put a
  non-idempotent, in-doubt-sensitive side effect (`"idempotent": false` runs, charges, deploys)
  inside a child flow; keep those as top-level `run` steps. `on_error`/`idempotent` are rejected on
  `flow` steps at load; the child owns its own failure policy internally.

## 4. State: two channels

**(a) The flowing pipe.** The previous step's value is the next step's input by default: `${in}` in a
template, the first argument of a `run` fn.

**(b) Named global state.** One JSON object, starting as `{"input": <your input>}`. Every step's
parsed value is **auto-stored under its id** (`$.classify`, `$.assess`, …) — read anything with
`${$.path}`, write with authored `set`/`append`/`delete` (§6). The pipe is sugar:
`${in}` ≈ `${$.<previous-step>}`.

## 5. Interpolation — one `${...}` engine

Every **string value** — `prompt`/`agent`/`ask` templates AND mutation source values — is literal text
with `${ ... }` holes:

| you write | means |
|---|---|
| `${$.a.b}` | the value at global-state path `$.a.b` |
| `${@.x}` / `${@}` | a field of / the whole of **this step's parsed value** (mutation scope) |
| `${in}` | the flowing input (template scope) |
| `$${` | a literal `${` (escape) |
| text with no `${...}` | a pure literal — `"approved"`, `"$5.00"`, `"@channel"` are safe |

Rules:
- **A lone hole preserves type** (`"${$.count}"` → the number `5`); embedded holes stringify
  (dicts/lists as canonical JSON). **Missing/null → `""`** in rendered text; reads used for logic
  (predicates, lone-ref sources) still resolve to null.
- **Non-string mutation values are literals as-is** (`{"$.ok": true}`).
- **Bare `$.path` (no `${}`) appears ONLY in structural positions** — mutation target keys, `when`
  predicate paths.
- **Currency caution:** `$${` is the escape, so never put a dollar sign directly before a hole —
  `"$${$.amount}"` renders as dead literal text. Write `"${$.amount} USD"` or reword.

Discipline: hole in **fields** (`${$.assess.recommend}`), not whole objects (`${$.assess}` stringifies
the entire object into the prompt).

## 6. JSONPath subset + authored mutations

Path grammar (identical everywhere, zero-dependency): **dot keys** + **`[int]` indices** (incl.
negative); roots `$` (global) and `@` (this step's value); no wildcards/filters/slices; a non-match
resolves to null.

```json
"set":    { "$.priority": "${@.priority}" },   // promote a field of this step's reply
"append": { "$.audit": "${@.summary}" },       // push onto a list (created if absent)
"delete": [ "$.scratch" ]
```

Mutations are **authored-only** (they live on the stepdef; models cannot emit writes — the one
exception is the `agent` kind's caller-captured `set_state` channel, §11). Target keys are bare `$`
paths; `set` creates intermediate objects; sources follow §5.

## 7. The model step — the task/router split

A `prompt`/`agent` step is **two narrow jobs, two calls**:

**The TASK call** does the work. Its conversation is built the same way every time:

```
[system]  engine prefix: "You are executing one step of a workflow. Do exactly what the
          instruction asks. Return your result as a single JSON object (the instruction may
          define its shape; else {\"result\": ...}). If — and only if — you cannot complete
          this without information from a human, reply with a single line starting `ASK: `..."
          + (when routed) the outcomes block: "give_refund: policy clearly allows...; ..."
[user]    the author's rendered directive — PURE, never mutated, nothing appended.
```

The reply walks a **discernment ladder**: an `ASK:` first line → a durable human gate (§10); else ONE
JSON object, tolerantly extracted (fences stripped, first balanced object) — not discernible → a
bounded repair prompt ("reply again as exactly one JSON object"); still failing → the can't-proceed
path (§10). The parsed object is the step's value — `${@.field}`, authored `set`, and `$.<step>.…`
are reliable on every step. Prose lives inside JSON strings (`{"email": "Dear customer…"}`).

**The exit is SELF-DECLARED, inspected mechanically (the fast path).** On a routed step the system
message LEADS with the expected-output contract — the outcome menu (label + `means`), stated before
the directive — and the task's strict JSON reply must include `"outcome"`. The engine inspects that
field: a declared label routes immediately (**zero judge calls**); `"proceed"` (on `"optional":
true` steps) falls onward; missing/off-menu triggers a bounded repair round.

**The ROUTER is the fallback.** Only when repair cannot produce a valid declared outcome does the
independent judge fire — a separate, **isolated** call (fresh context, own system message,
cheap-model eligible via `load_workflow(..., router=)`) given the directive, the output verbatim,
and the menu. It replies `{"outcome": "<label>"}`, `{"outcome": "proceed"}` (optional steps), or
`{"outcome": "ask", "question": "<why it cannot clearly route + what would disambiguate>"}`; its
own repair exhaustion **forces** the reasoned `ask`. The judge remains the independence safety net
and the owner of the can't-route path — it is no longer the toll on every branch.

Why the split: the task prompt stays clean (your directive IS the prompt), routing becomes a
classification problem (what small models are reliable at), the worker can't grade its own homework,
and every branch decision is an independently journaled verdict against stated conditions — an audit
trail one-call designs can't produce. The cost: two calls per judged branch (see Pitfalls).

**One line:** the TASK does what the directive asks and DECLARES its exit (`"outcome"` per the
prefixed contract, inspected mechanically); the JUDGE fires only as the fallback (and owns the
reasoned ask); the WORKER may interrupt with `ASK:`; writes are authored `set` over `${@...}`.

## 8. Routing — the resolution chain

Evaluated in order, first hit wins:

1. **`when` predicates** — mechanical rails, free, checked before the router:
   `{"if": {"path": "$.amount", "gt": 1000}, "to": "review"}`. A predicate is a bare `$`-path
   (truthiness), a `{path, <op>}` compare (`eq`/`ne`/`gt`/`gte`/`lt`/`lte` — TOTAL: a missing path or
   cross-type compare is simply false), or a registry fn name `(state, result) -> bool`. Exactly one
   operator per predicate, validated at load.
2. **The routed label** — the step's self-declared `"outcome"` (prompt/agent, inspected
   mechanically; the router's verdict on fallback) or the human's answer (`ask`), mapped through
   `routes`. Routes are **binding**: the judge must pick a declared label, and an `ask`
   state's `options` must all be mapped (validated at load) — unless the state says
   `"optional": true`, which legalizes "none of these → fall onward". A strict routed step
   declaring `next` is rejected at load (that `next` would be unreachable dead spec).
3. **Declared `next`** — an explicit unconditional edge (jumps forward, loops back).
4. **Sequential fall-through** — the next state in declaration order; the last declared state →
   `@done`.

Routes grammar: shorthand `"valid": "payout"` or the object form
`"valid": {"to": "payout", "means": "the claim fully satisfies policy"}` — `means` is the condition
both the task and the judge are shown (the label itself when absent).

**The edge-decision rule:**

| the condition is… | use |
|---|---|
| mechanical (a value, threshold, range) | a `when` rail |
| a judgment (classify, assess) | `routes` + the router |
| consequential / human-owned | an `ask` gate |
| unstated | nothing — fall through |

## 8a. Failure routing — `on_error`, `on_item_error`, `on_exhausted`

Without failure policy, a throwing `run`/`search` (or one failed `map` item) fails the **whole flow**
(exit 1), and a model step that exhausts its budgets fails cleanly too. These keys walk failure paths
as data instead.

### `on_error` (run/search) — an ordered matcher ladder

```json
"fetch": { "run": "fetch_data",
           "on_error": [
             { "match": "Timeout|ConnectionError", "retries": 3, "backoff_ms": 500 },
             { "match": "*", "to": "cleanup", "result": {"items": [], "why": "${@.__error__.message}"} }
           ] }
```

Rules are tried in order on each failing attempt; **the first rule whose `match` regex (searched
against `"<name>: <message>"`; `"*"`/absent = match-all) matches wins fully**: retries left → retry
(with its `backoff_ms`, exponential); else `to`/`result` present → **catch**; else → raise (exit 1).
A single object is sugar for a one-rule list. Regex semantics are pinned portable: ASCII classes,
newline-stripped haystack, and `(?...` constructs beyond `(?:` `(?=` `(?!` rejected at load.

**What a catch does.** The failure is memoized as a synthesized `step_completed` whose result is the
**error sentinel** `{"__error__": {"name", "message", "attempts"}}`. `$.<state>` holds the sentinel
(addressable by templates/predicates) and it flows — unless the rule has `result`, in which case the
**deeply resolved** fallback replaces it (`@` bound to the sentinel). `to` routes there directly,
bypassing normal routing. The branch is **replay-deterministic** (the sentinel is memoized; a revisit
via a loop gets a fresh per-visit key and re-executes). `__error__` is reserved.

### `on_item_error` (map) — per-item policy

`"fail"` (default) = one item throw kills the flow. `"collect"` = the item's sentinel stays in the
list at its position. `"skip"` = memoized but omitted from the list. Map-level `retries`/`backoff_ms`
apply per item. Non-`fail` requires a `run`/`search` inner.

### `on_exhausted` (prompt/agent) — declarative exhaustion routing

```json
"assess": { "prompt": "...", "routes": {"ok": "fulfil", "bad": "@fail"},
            "on_exhausted": { "to": "manual_review",
                              "result": {"gave_up": true, "last": "${@.raw}"} } }
```

When a model step's budgets truly exhaust (JSON repairs spent, or `max_intervene` feedback rounds
spent), the step's value becomes `result` (deep-resolved; `@` = the **last task output** — the parsed
object, or `{"raw": "<text>"}` when the last reply never parsed) or an `Exhausted` sentinel, and `to`
routes out-of-band. **Without `on_exhausted`, the flow fails cleanly** (exit 1, a clear message) —
there is no route-picking gate: humans inform through the ask loop (§10); they never hand-pick edges.

### `"idempotent": false` (run) — crash-window escalation

The run fn contract widens to `(flowing, state, idem_key)`; a process death mid-step escalates
in-doubt (exit 11) → `resume --resolve completed|retry|abort`. `on_error` handles application
**throws**; in-doubt handles crash **uncertainty** — they compose, never overlap.

## 8b. Cycles

The graph is arbitrary: edges may point backward, forward past several states, or at the state
itself. **Per-visit keys** make cycles durable: each arrival gets a fresh key (`write#0`, `write#1`),
so a loop iteration re-executes while resume-replay reuses every completed visit. State on loops:
`$.<state>` holds the **latest** visit's value (overwrite); use `append` to keep history.

The cycle taxonomy: **human-bounded** (an `ask` gate in the loop — the revision workhorse, always
safe), **data-bounded** (a `when` rail — safe), **model-bounded** (prompt↔prompt — bounded only by
`max_visits`, default 25, spec-overridable; exceeding it fails with a clear message). Two rules:
junction states (a back-edge target beside a fall-through neighbor) should declare `next` explicitly,
and every cycle needs an exit (a gate, a rail, or a judged route that eventually leaves).

Clock-driven cycles ("poll every 5 minutes") need durable timers — deliberately deferred; the interim
pattern is an external scheduler re-invoking `resume` (free, thanks to replay).

## 9. Conversational context — the workflow IS an agent in one context (by default)

`prompt`/`agent` steps **share one flow-wide conversation by default**: each step is a directed
prompt into a persistent agent context — later steps see earlier directives and replies as turns.
**Gates are turns too**: an `ask` step on the thread contributes its question (assistant turn) and
the human's answer (user turn), so a revision loop's next lap SEES the verdict without state holes
(put `"context": "isolated"` on a gate to keep it out). The transcript is rebuilt from journaled
responses on every replay, so the agentic mode is durable for free.

**Isolation is the opt-out dial**, per step or flow-wide:
- step-level `"context": "isolated"` — drop one step out of the thread (lean: engine prefix + its
  own directive only); a named label runs a separate side thread.
- spec-level `"context": "isolated"` — the whole flow runs lean (each step sees only its own
  directive + `${...}` holes); any other spec-level label just renames the flow thread.
- Always true regardless of the dial: `map` items get FRESH histories (pure fan-out), the router
  judge is always isolated, and interrupt weaves are within-step.

**When to flip a section to isolated** (the regimes where lean wins):
| stay on the thread (default) | flip to isolated |
|---|---|
| short flows (≈≤10 model steps) | many steps / loops (the thread grows — quadratic token cost) |
| exploratory work: research, debugging, drafting (texture carries) | cheap/small models per step (long contexts degrade them) |
| one capable model end to end | days-long suspensions, audit-heavy flows (inspectable state beats a prose transcript) |
| low audit needs | cross-run / nested composition (structured `$.…` handover) |

Either way, **`${...}` holes remain the explicit, reliable data channel** — concrete values
(names, amounts, findings) should be rendered into directives even inside a thread; the thread
carries texture, the state document carries facts.

One more thing worth knowing: an agent-in-one-context at length always ends up needing compaction
(that is why coding agents have /compact). Explicit state is that same move done continuously and
structurally — authored `set`/`append` is compaction with the author choosing what survives. The
dial exists so you can pick per section.

## 10. Interrupts — three sources, one gate

All three land on the same durable intervene gate and the same resume semantics:

1. **The worker's `ASK:` line** — any prompt/agent step (routed or not) replies
   `ASK: <question>` when it cannot proceed without human input. The engine teaches this rule in the
   system prefix on every call; authors never write interrupt plumbing.
2. **The router's `ask` verdict** — the can't-route path: the judge states WHY the output doesn't
   clearly satisfy an outcome; that reason IS the question the human sees.
3. **An authored `ask` state** — the planned checkpoint, with your wording and typed options.

On resume, the answer is woven into the **task** conversation as one standardized turn ("The human
answered: … Continue the instruction with this information."), the task **re-attempts**, and (when
routed) a fresh router round judges the new output. Bounded by `max_intervene` (default 3). Use
authored `ask` gates for checkpoints you know about; `ASK:`/router-`ask` are for surprises.

**Gates are external-event gates, not just human gates.** Nothing in suspend/resume knows the
answerer is a person: `resume --answer` can be called by a webhook handler, a Slack approval bot, CI,
another agent, or a scheduler applying a default after a deadline. "Prompt the user" is really "wait
for the world".

## 10a. How resume works (the walk, not the engine)

**No interpreter state is stored** — no snapshot, no pickled stack. When a flow suspends, two data
structures survive: the **spec** (which graph) and the **journal** (append-only: every model reply,
every answer, every step value, in order). `state.json` is a convenience mirror, never the truth.

**Resume = re-walk the spec, driven entirely by journaled values.** The interpreter loop is a walk of
the data structure (`current = start; look up states[current]; memoized sub-steps return recorded
values; route; hop`). On resume every `ctx.step` finds its recorded result (no re-execution, no model
calls), every gate finds its recorded answer, every routing decision re-derives from recorded values
— so the walk re-arrives at the parked position in milliseconds, as a **pure function of
(spec, journal)**. The position is itself data: the pending key `assess#2/intervene#1` reads "state
`assess`, third visit, second interruption" (nested flows compose the key and a `chain` —
`vet#0/gate#0` — and a parked child's ENTIRE journal is embedded as data inside one parent record,
recursively, so the "call stack" is a recursive data structure in one file). Re-walking
instead of jumping to the parked node is deliberate: the walk **self-validates** (any spec/journal
divergence is detected as skew, exit 3, instead of resuming into a wrong position), it revalidates
every memoized model call against the conversation it now demands (`in_hash` — the identity of the
WORK, not just the workflow; a mismatch discards and re-executes, journaled as `memo_invalidated`),
reconstructs
conversations/map progress/nested calls with one mechanism, and costs nothing. Editing the flow while
a run is parked is refused by the flow-hash guard until you pass `--accept-flow-change`.

## 11. The `agent` kind + live state (MCP)

`prompt` steps get state by substitution; `agent` steps additionally read/write state **live** via a
tiny MCP server (`scripts/state_mcp.py`, zero-dep stdio JSON-RPC exposing `get_state`/`set_state`).

- Register once: `hermes mcp add state --command python3 --args /abs/scripts/state_mcp.py`
  (interactive tool-enable confirm; validate with `hermes mcp list`).
- The default caller runs `hermes -z -t all` (`-z` keeps MCP) with the flattened convo — the engine
  scaffold rides as the leading `system:` line.
- **Determinism:** `get_state` is read-only; every `set_state` is captured by the caller and returned
  on the `{"text": ..., "set": {...}}` channel, folded into the journaled step value — replay
  re-applies the recorded writes without re-running the agent or the server. This caller-captured
  channel is infrastructure, not model-emitted mutation: the model still cannot invent state writes.
- Constraint: `agent` needs a real `--state-dir` (no `ctx.call` children / `run_flow`).

## 12. JSON (canonical) or YAML (authoring)

The interpreter takes a parsed dict. **JSON is canonical/portable** (what an LLM emits and what we
validate/repair); `read_spec`/`load_workflow_file` also accept `.yaml`/`.yml` iff a YAML parser is
importable. YAML buys block-scalar prompts and comments; the data is identical.

## 13. Headless

Under `--auto` / `HERMES_HEADLESS=1`, gates resolve via a schema `default` or the interpreter hook
(else exit 12). Keep `options` meaningful on `ask` gates so autonomous runs stay intentional; note
worker-`ASK:` and router-`ask` gates are free-text (no enum), so headless runs need an interpreter
hook or should treat exit 12 as "a human is genuinely required".

## Pitfalls (read before authoring in anger)

1. **Judged branches cost two calls** (task + router), and 2× per lap in cycles through them. `when`
   rails are free — never spend a model call on arithmetic. Keep route menus small (2–4 labels).
2. **The judge only sees the output — never the thread.** On a shared thread a task can produce
   context-dependent prose ("as established above…") whose referents the judge cannot see. The
   scaffold now tells the model so explicitly ("the judge sees ONLY this output, not the
   conversation"), and directives on routed steps should ask for EVIDENCE, not just a verdict.
3. **Declaration order is load-bearing.** Reordering states changes fall-through behavior, and a
   forgotten `routes` block proceeds silently. Declare `next` at junctions.
4. **Isolated context shifts memory onto templates.** Forgetting both a state hole AND a context
   label means the model hallucinates the missing background, silently.
5. **Prompt bloat**: hole in fields, never whole objects.
6. **`ASK:` is a string convention.** A worker phrasing its need conversationally isn't detected on
   an UNROUTED step (routed steps have the router-`ask` net). A legit output starting "ASK:" is a
   false positive. Accepted trade.
7. **Mid-suspension spec edits are SAFE (selectively)**: the flow-hash gate still refuses first;
   after `--accept-flow-change`, in-hash memo validity re-executes exactly the model calls whose
   rendered conversations changed — and the cascade follows OUTPUTS (a changed upstream reply
   re-executes downstream calls; an edit that yields the identical output leaves downstream
   memoized). Human answers are never re-asked. v1 boundary: `run`/`reduce` steps declare no input
   hash, so a code step downstream of an edit keeps its memo — re-run in a fresh dir if that matters.
8. **Model-bounded cycles** are capped at `max_visits` (default 25); human/data-bounded loops never
   notice.

---

## How this compares (design rationale)

A JSON state machine + a JSONPath subset is the shape of **AWS Step Functions / Amazon States
Language** and **Netflix Conductor**; our `$` global vs the flowing pipe mirrors ASL
`ResultPath`/`OutputPath`; `when` ≈ `Choice`, `routes`+`next` ≈ `Next`, `set/append/delete` ≈ Google
Cloud Workflows `assign`. We use a **delimited `${...}`** interpolation (like Conductor and Cloud
Workflows) rather than value-inspection — a literal like `"$5.00"` is never misread as a path. We do
**not** need a richer expression language (AWS added JSONata in 2024) because **the model is our
transform engine**; the interpreter only selects, routes, and stores. The durable engine underneath
is Temporal/Inngest-like (deterministic replay + memoization).

**The task/router split** is the generator/discriminator pattern applied to routing: the worker
produces evidence, an independent judge classifies it against author-stated conditions (`means`).
Engines like Conductor/ASL route on expressions over structured output; we route on *judgment over
free output* — which is exactly the part LLMs add — while keeping expressions (`when`) for everything
mechanical. Because the engine owns ALL prompt scaffolding (one constants block), improving one
paragraph improves every workflow retroactively, and worker/judge/answerer can be different models.

**Injected-tool kinds — the generalization rule.** `search` is deliberately the *only* web-specific
kind. The **second** injected tool triggers a generic `{"call": "<name>", "args": {…}}` kind with
`load_workflow(..., tools={name: fn})`, and `search` becomes load-time sugar over it. Durable keys
don't encode the kind, so existing journals replay unchanged across that migration. (Naming note:
`references/nested-flows.md` reserves a future child-FLOW kind also sketched as `"call"` — whichever
lands first picks a non-colliding name.)
