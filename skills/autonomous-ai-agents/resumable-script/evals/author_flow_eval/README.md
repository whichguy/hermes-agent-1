# author_flow_eval — does a real LLM author runnable workflow specs?

Every rung in `tests/` runs a **hand-written** spec. This eval tests the skill's reason for existing:
a **real Hermes agent authors** a workflow spec in the `${...}` format, the engine runs it, it
**suspends at a human gate**, a **real LLM answers as the human**, and the flow works its way around
the (possibly cyclic) graph to the expected terminal — author → run → interrupt → answer → resume →
complete, end to end, across suites of increasing complexity. Real models play every role; the harness
is the deterministic referee.

## Grading is behavioral, not structural
Tasks describe the JOB — never option labels, state names, or step kinds. The model authors its own
graph. The harness checks bare invariants (spec validates; the run suspends at least once when a
completion is expected; a terminal is reached within the resume budget; the terminal matches the
scenario's `expect`) plus per-scenario **evidence** over what actually happened:

- **Canaries prove interpolation.** Distinctive values live only in the run's *input* (the authoring
  model never sees them — only the input's field-name *shape* is shared). A canary appearing in a
  rendered gate question (`pending.question.prompt` in the suspended payload) proves the model authored
  a `${$.…}` hole and the engine filled it at runtime.
- **Routing is graded by outcome.** L3 gives the same task a valid and an invalid input and requires
  opposite terminals (`completed` vs `@fail`) — the model's runtime `next` choice is what's measured.
- **Cycles are read from the journal.** Step keys are `<state>#<visit>`; a visit ≥ 1 means a route
  genuinely looped back.

## Shape: one test, many suites
There is exactly one test body — `test_authoring_e2e(scenario)` — and every suite points at it.
`scenarios.SCENARIOS` is a dict of suites (`L1`…`L6`); each scenario becomes a parametrized case tagged
with its suite's marker.

```
cheatsheet.py   the ${...} format taught to the authoring model + the fixed run-fn names
authoring.py    author_spec() -> hermes -z (via oneshot.run_docker_exec) + bounded repair loop
wrapper.py      write_flow() -> runnable flow file; binds BOTH real-model callers (llm= and agent=)
bridge.py       docker_agent_caller (agent kind, MCP state) + docker_llm_caller (prompt kind)
answerer.py     a real LLM plays the human at each suspension, steered by the scenario's `intent`
driver.py       run_scenario() -> THE one shared e2e function (invariants + evidence live here)
scenarios.py    SCENARIOS = {"L1": [...], ...} — pure data, the suites
test_*.py       one parametrized test; suites are markers
conftest.py     live_env fixture — skips (never fails) when Hermes/Ollama are down; registers state MCP
env_setup.py    probes + one-time state-MCP registration in the container
```

## Running
```bash
# one-time
python3 -m venv evals/.venv && evals/.venv/bin/pip install pytest

# a single suite / the whole ladder (run from evals/)
evals/.venv/bin/pytest author_flow_eval -m L1 -v
evals/.venv/bin/pytest author_flow_eval -v
```
Requires the `hermes` container (`docker ps`) and Ollama at `localhost:11434`. With either down, every
case is **skipped**, not failed. Backend defaults to whatever `hermes -z` is configured for
(`glm-5.2:cloud`); override with `RESUMABLE_EVAL_MODEL` / `RESUMABLE_EVAL_PROVIDER`.

## Non-determinism is data, not hidden
Author, workflow steps, and the gate-answering human are all real models, so each scenario has an
`attempts` budget and a `max_resumes` cycle budget; a pass on any attempt passes the scenario, and
every attempt's evidence (authored `spec.json`, `authoring.json`, per-step engine payloads,
`*_answers.json` gate transcripts, `*_journal.jsonl`) lands under `artifacts/<suite>_<id>/attemptN/`.
The last valid authored spec per scenario is saved to `fixtures/<id>.json`. Both dirs are git-ignored.

## Suites (increasing complexity)
| Suite | What must actually happen | Evidence |
|---|---|---|
| L1 | approval flow pauses; the question shows the request's concrete details | input canaries in the rendered gate prompt |
| L2 | an LLM step stores a finding; a later gate surfaces it | constrained finding (`high`/`low`) + reference canary in the prompt |
| L3 | a model step routes at runtime on state validity | same task: valid input → completed, invalid → `@fail` |
| L4 | a reviewer demands a revision; the graph loops back | ≥2 suspensions + a `#visit≥1` journal key |
| L5 | run 2 (same authored flow) references run 1's final state | run-1 canary + run-2 canary both in run 2's gate prompt |
| L6 | an agent reads state via `get_state`, records a risk via `set_state` | customer canary in prompt + `low/medium/high` leaf in final state |

## Format findings this eval has surfaced
- Authoring models invent input field names unless given the input's **shape** — the harness now
  auto-derives field names/types (never values) from each scenario's input and appends them to the task.
- The `$${` escape collides with natural currency phrasing: `"for $${$.amount}"` renders as dead
  literal text, and there is currently **no way** to express a literal `$` immediately before a hole.
  The cheatsheet warns authors off it; a real fix would be an engine-level escape change (e.g.
  docker-style `$$` → `$`, which keeps `$${` → `${` and adds `$$${hole}` → `$<value>`).
