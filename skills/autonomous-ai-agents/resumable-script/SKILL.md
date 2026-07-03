---
name: resumable-script
description: >
  An LLM authors a JSON/YAML workflow spec (states, prompts, routing, failure policy); a durable
  interpreter runs it, passing state from step to step, with suspend/resume, human gates, and
  replay that never re-runs completed steps. Use when an LLM should define-and-run a multi-step
  workflow that must pause for a human (approve, decide, fix something) and resume later, survive
  a crash and continue exactly once, or route declaratively on step errors. Rides a
  durable-execution-lite engine (append-only journal, deterministic replay). Triggers:
  LLM-authored workflow, workflow spec, prompt routing, durable resume, resumable script,
  pause and resume, checkpoint and continue, don't re-run completed steps.
version: 0.2.0
author: agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    category: autonomous-ai-agents
    tags: [durable-execution, resumable, workflow, llm-authored, journal, replay, memoization, human-in-the-loop, checkpoint]
    related_skills: [method-explorer]
    config:
    - key: resumable-script.blob_threshold
      description: Step results larger than this many bytes spill to a blobs/ sidecar file
      default: 65536
      prompt: Byte threshold above which step results spill to a blob?
---

# Resumable Script — LLM-authored durable workflows

## Overview

**The product is a data file.** An LLM (or a human) writes a JSON/YAML **workflow spec** — a state
machine of `run` / `prompt` / `agent` / `search` / `map` / `ask` / `flow` (nested child workflow)
steps with `${...}`-interpolated
prompts, routing, and declarative failure policy — and a small interpreter (`scripts/workflow.py`)
compiles it onto a durable engine. The author writes **prompts + routing + a function registry,
not control flow**; the engine provides durability, deterministic replay, suspend/resume, and
human-in-the-loop for free. Model steps share ONE flow-wide conversation by default (the workflow
is an agent in a single context; `context: "isolated"` — per step or spec-wide — is the lean
opt-out), and state passes step to step automatically: each step's result flows to the next
(`${in}`) and is stored under its name (`$.<step>`), readable from any later prompt.

A run lives in one directory (an append-only `journal.jsonl`). To resume — after a human answer
or a crash — the engine re-runs the workflow from the top, replaying journaled results instead of
re-executing them. Completed steps never re-run; the call stack is never serialized.

**Authoring guide (the spec grammar, kinds, interpolation, routing, failure policy):
`references/workflow.md`. Read that first.**

## Design requirements & where they're proven

This skill exists to satisfy four specific requirements. Each is a claim about *behavior*, not
just intent — so each one below is traced to the exact mechanism that implements it and the exact
test that proves it, not just asserted in prose. Line numbers are current as of git commit
`6c73960` in this skill's own repo; re-`grep` if the code has moved.

**1. Structured — one append-only journal, deterministic replay.**
A step whose key already has a `step_completed` record returns that result without re-executing
the step body: `scripts/engine.py:419` (`if key in memo.completed: return memo.completed[key]`).
The journal has ten record types, each with a fixed shape (`run_started`, `step_started`,
`step_completed`, `step_failed`, `ask_requested`, `ask_answered`, `in_doubt_resolved`,
`flow_changed`, `memo_invalidated` (in-hash memo validity: a model call's memo replays only if the conversation it was produced by is byte-identical to the one now demanded — edits selectively re-execute, cascade included, human answers survive), and `call_suspended` — the last appended by `Context.call` when a nested child
flow suspends, embedding the child's entire portable state) — see
`references/journal-format.md` for the full contract and each record's fields.
*Proven by:* rung `l01` (`tests/run_ladder.py:210-217`) — runs a flow twice and asserts the
step's underlying function fires exactly once (`count_started("compute") == 1`) while the result
stays identical across both runs.

**2. Prompt by default — the authoring surface is prompt/agent steps, not imperative code.**
`prompt` and `agent` are first-class kinds in `_KINDS`, dispatched through `_do_model_step`
(`scripts/workflow.py`) — the v2 TASK/ROUTER split: the TASK call runs the author's directive as a
PURE user message under an engine-owned system prefix (one auditable constants block: JSON-result
rule, `ASK:` interrupt rule, the outcomes block) and replies with ONE discrete JSON object
(tolerantly extracted, bounded repair); routed steps LEAD the system message with the expected-output
contract and the reply's `"outcome"` field is inspected MECHANICALLY (fast path, zero judge calls);
a separate, isolated ROUTER call (`_run_router`; `router=` caller, defaults to llm) is the FALLBACK
when repair can't produce a valid declared outcome — binding menus, `proceed` only on
`"optional": true` steps, `ask` = the reasoned can't-route path (the answer is woven into the TASK
convo and the task re-attempts). Writes are AUTHORED-ONLY (`set` over
`${@...}`). *Proven by:* rungs `wf_return` (task repair + router repair + authored set),
`wf_scaffold` (byte-exact directive, one system message, standardized weave), and `wf_router`
(strict/optional/ask/forced-ask/rail-skip).

**3. State carrying — a flowing pipe plus named global state.**
`state = {"input": inp}` and `flowing = inp` are seeded once (`workflow.py:536-537`); after every
step, `state[current] = result` (auto-store at `$.<step>`, `workflow.py:562`) and `flowing = result`
(the next step's default input, `workflow.py:578`). Both are read back through the same `${...}`
engine (`${$.path}` global, `${in}` flowing — `references/workflow.md` §5). *Proven by:* rung
`wf_state` (`tests/run_ladder.py:1113`) for both channels in one flow, and `wf_paths`
(`tests/run_ladder.py:1252`) for the full interpolation golden case (index, missing→`""`, `$${`
escape, lone-ref type preservation) carried across an actual suspend/resume.

**4. The LLM can interrupt for a user response, and the interrupted prompt is reentrant.**
A `prompt`/`agent` step interrupts by replying with an `ASK:` first line (taught by the engine
prefix on every call; detected by `_as_decision_request`), and the ROUTER interrupts with a
reasoned `ask` verdict when an output can't be clearly routed; both raise the same durable human
gate via `ctx.ask`, which exits the process cleanly (exit 10) rather than blocking. **Why
it's reentrant, precisely:** each round of the dialogue is its own memoized sub-step keyed
`f"{skey}/{tag}#{r}"` (`workflow.py:741-742`), and the conversation `history` is rebuilt from
those journaled results on every pass (`history = histories.setdefault(label, [])`,
`workflow.py:734`, populated only by replaying already-completed `ctx.step` calls plus
`ctx.ask` answers, `workflow.py:757-758,762-763`) — there is no separate serialized "conversation"
record. A replayed round never re-invokes the injected LLM caller; only a genuinely new round does.
*Proven by:* rung `wf_intervene` (`tests/run_ladder.py:1195`) for a single interruption, and rung
`wf_intervene_multi` (`tests/run_ladder.py:1214`, fixture `tests/ladder/wf_intervene_multi.py`) for
the **full** multi-round claim — a dialogue that suspends twice (round 0 asks, round 1 asks again,
round 2 resolves) asserts after *each* resume that every earlier round's call count is still
exactly 1, i.e. resuming into round 1 does not re-invoke round 0, and resuming into round 2 does
not re-invoke rounds 0 or 1. (This second rung was added specifically because the single-round
`wf_intervene` rung could not distinguish "reentrant" from "merely resumable once.")

Run `python3 tests/run.py` to re-verify all four (and everything else) at once; `python3 tests/run_tiers.py` climbs the complexity tiers simplest-first (ground-truth artifact `tests/.last_run.json`); `./tests/ask_run.sh [tiers|<suite>]` runs them through the ask skill (an in-container agent executes; the artifact is the verdict).

## Quickstart (the worked example)

`examples/triage.workflow.json` is a refund-triage workflow: `search` (research) → `map`/reduce
(summarise each line item) → `prompt` (assess; the ROUTER judges auto/review) → `ask` (human
approval gate) → `run` (notify). `examples/triage.py` is its host driver with stub callers, so it
runs offline. The canonical multi-step v2 example is `examples/complaint.py` (+
`complaint.workflow.json`) — both traces from `references/workflow.md` §1, including the `ASK:`
interrupt, run offline:

```bash
python3 examples/triage.py run --state-dir /tmp/tri \
  --input '{"customer":"acme","topic":"widget","items":[{"name":"A","amount":60},{"name":"B","amount":80}]}'
# -> exit 10, suspended at the approval gate; the payload carries pending.question
python3 examples/triage.py resume --state-dir /tmp/tri --answer '"approve"'
# -> exit 0, completed; memoized steps (search, per-item summaries) did NOT re-run
```

## The host driver file

A spec needs a small host file that supplies the things data can't: the **registry** of plain
functions for `run` steps, and callers for `prompt` / `search` / `agent` steps. That's the whole
integration surface:

```python
# triage.py
from workflow import load_workflow_file

def tally(items, state):            # run fns are (flowing_input, state_snapshot) -> result
    return {"total": sum(i["amount"] for i in items)}

REGISTRY = {"tally": tally, ...}
flow = load_workflow_file("triage.workflow.json", REGISTRY,
                          llm=my_llm_caller,        # llm(convo) -> RAW text (workflow.md §2/§7)
                          router=my_judge_caller,   # optional; defaults to llm (a cheap model fits)
                          search=my_search_caller)  # search(query, format) -> results dict
```

A spec that uses a kind whose caller wasn't injected **fails at load**, not mid-run.

## Driving a run

Every invocation prints one JSON status line (parse the **last** stdout line) and exits with a
distinct code — the whole orchestration contract for an agent driving the loop. A headless
driver that would rather poll a file than capture a subprocess's stdout can pass
`--output-file <path>` (or set `HERMES_OUTPUT_FILE`) to redirect that same line there instead:

```
python3 scripts/engine.py run    --flow triage.py --input '<json>' --state-dir D
  exit 0  -> completed; result in payload
  exit 10 -> suspended; surface pending.question, then:
             python3 scripts/engine.py resume --flow triage.py --state-dir D --answer '<reply>'
  exit 11 -> a non-idempotent step is in doubt (crash mid-step); resolve with
             resume --resolve completed|retry|abort
  other   -> walk references/driving-failures.md (decision tree keyed by exit code + stderr)
```

The journal on disk **is** the state — resume needs only the same `--state-dir` and the answer.
Full contract (payload shapes, all exit codes, headless `--auto`, `--accept-flow-change`):
`references/cli-contract.md`.

## Failure policy (declarative, in the spec)

Steps can handle their own failures instead of stopping the driver: `on_error` (an ordered
matcher ladder on `run`/`search` — retry budgets, route-on-error, fallback results),
`on_exhausted` (route when a `prompt`/`agent` exhausts its repair/intervene budget),
`on_item_error` (per-item policy inside `map`), and `idempotent: false` (a crash mid-step
escalates to in-doubt instead of risking a double-apply). Details: `references/workflow.md`.

## The `agent` kind + live state (MCP)

An `agent` step runs a full Hermes agent (`hermes -z`) that can read/write workflow state live
via `scripts/state_mcp.py` (a zero-dep stdio MCP server exposing `get_state`/`set_state`);
captured mutations are folded into the journaled result so replay never re-consults the agent.
Register once: `hermes mcp add state --command python3 --args -- <abs>/state_mcp.py`. See
`references/workflow.md` §11.

## Substrate: the code-first engine

The interpreter rides `scripts/engine.py` — a durable-execution-lite engine (Temporal/Inngest
pattern in one zero-dep file) that can also run **hand-written flows**: ordinary functions whose
side effects are wrapped in `ctx.step(key, fn)` and whose gates are `ctx.ask(key, question)`.
This code-first surface is substrate, not the product; reach for it when a flow genuinely needs
arbitrary host-language control flow.

- Cardinal rule for hand-written flows: all side effects and non-determinism inside `ctx.step`;
  code between steps must be pure (it re-executes on every resume). Determinism rules:
  `references/authoring-flows.md`; minimal example: `examples/provision_db.py`.
- **Nested flows:** `ctx.call(key, child_flow, input)` invokes an INDEPENDENT, reusable child
  `Flow` — not just a namespaced helper sharing this flow's journal (that's what `map`/the
  `prompt` intervene loop already do for free, see design requirement 4 above). A suspend
  anywhere in the chain bubbles up automatically to arbitrary depth, and `run_flow`/`resume_flow`
  (a library API alongside the CLI) return/accept the **entire** resumable state as ONE
  self-contained, portable JSON value — no `--state-dir` required. Read
  `references/nested-flows.md` before reaching for this: it documents a real crash-safety
  trade-off you're accepting (a `ctx.call` child is always in-memory, never durable mid-pass).
- **Flagship composition example:** a durable, resumable codebase investigation
  (`examples/investigate_repo.py` — map → reproduce → classify → locate → focus → inspect →
  propose → approve → apply-fix → verify), exercising memoized scans, fail-fix-resume, decision
  gates, flaky-retry, and a crash-mid-edit in-doubt. Runs against a fixture backend (hermetic)
  and a real repo (`tests/run_integration.py`). Guide: `references/investigation-flows.md`.
- **Narrated tours:** `python3 examples/walkthrough.py` (crash → in-doubt → resolve → gate →
  free replay), `examples/walkthrough_investigate.py`, and `examples/walkthrough_nested.py`
  (nested `ctx.call` + portable state: hoisted suspend → one-JSON-value run → verbatim-key
  resume with an exactly-once proof → the fork risk).

## Common Pitfalls

- **Spec authors:** a typo'd `${$.path}` renders as `""` (missing → empty) — check paths against
  the auto-store names (`$.<step>`). Routing labels must match `routes` keys exactly. `map`
  inners are pure fan-out: routing/mutations belong on the map state itself (rejected at load).
- **Registry fns** receive `(flowing, state_snapshot)` — mutate nothing; return a JSON-safe value.
- **Hand-written flows only:** a side effect outside `ctx.step` re-fires on every resume;
  un-stepped non-determinism diverges replay (exit 3); reusing a step key is fatal (exit 2);
  non-idempotent steps should forward the injected `idem` key so downstreams dedupe.

## Verification Checklist

- `python3 tests/run.py` — JSON-contract checks, the JSONPath/`${...}` golden matrix
  (`tests/paths_cases.json`), then the full escalating ladder (mechanism L00–L13,
  property/coverage, e2e, workflow `wf_*`, nested `ctx.call` `call_*`, agentic-investigation
  `inv_*`), then the separate `run_call_ladder` (the `run_flow`/`resume_flow`/
  `export_portable_state` library API — `references/nested-flows.md`); halts at the first
  failing rung with a journal diff. `--with-integration` adds the real-repo tier; `--evidence`
  prints per-rung receipts (exact CLI calls, exit codes, gates). The journal format + golden
  fixtures are the language-neutral contract (a quarantined JS mirror lives in
  `extras/js-mirror/`; see `references/journal-format.md` §Portability).
- `python3 tests/run_ladder.py [-k wf] [--evidence]` — climb/filter directly.
  `python3 tests/run_call_ladder.py [-k rf_2level]` — the library-API ladder standalone.
- **Named suites** (`tests/suites.py`) — pure indexes into the rungs of BOTH ladders by name; a
  rung may belong to several suites. `--list-suites` lists them (and validates every name
  against the union of the two `RUNGS` registries); `--suite smoke` runs the minimal
  breadth-first set (~27 rungs touching every major feature area once);
  `engine`/`workflow`/`investigation` split by architectural layer;
  `failure-policy`/`reentrancy`/`nested-call` are cross-cutting concerns (`nested-call` spans
  both surfaces: the CLI `call_*` rungs and the library-API `rf_*` rungs, ~28 rungs).
- Manual smoke: the Quickstart above — run triage, answer the gate, confirm memoized steps did
  not re-run (inspect `journal.jsonl`).
