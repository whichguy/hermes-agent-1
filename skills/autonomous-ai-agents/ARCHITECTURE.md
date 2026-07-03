# The skill family — roles, state contracts, call graph, isolation rules

*Written 2026-07-03 after the family-wide duplication audit and consolidation
(intent-to-tasks retired into task-decomposer; define-done wired via `--dod`;
resilient-planner renamed method-explorer). Extended same day with the topology-B
decision (DIRECT layer, scope mode, needs_split, recursion guard, global knowledge
tier). This is the durable record of the architecture verdicts; per-skill mechanics
live in each skill's SKILL.md/DESIGN.md.*

## Roles — one skill per role

| Role | Question it answers | Occupant | Character |
|---|---|---|---|
| **DIRECT** (own) | What problems, in what order, and when do we stop overall? | **jim's dev loop — outside the family** | the ONE layer above relentless (topology B, 2026-07-03); calls `scope` for planning and `solve` per hard step; holds endeavor-level stop authority |
| **SPECIFY** (what) | What does done mean? | define-done | LLM-authored `dod.md`; `spec.py` is the pure parse/lint library |
| **CLARIFY** (know) | What don't we know that's worth knowing? | investigator (loop) + next-best-questions (EVSI ranker) | investigator wraps the ranker; deployed-tree only (see Regimes) |
| **MAP** (decompose) | Given intent + knowledge, what tasks next? | task-decomposer | stateless oneshot → `plan.json`; also owns the completion-report schema (`report.py`) |
| **ORCHESTRATE** (loop) | Keep going honestly until success / dry / decision — for ONE problem | relentless-solve | the only family component that knows the others exist; a per-problem SUBROUTINE under DIRECT (`solve` = do it; `scope` = the CLARIFY→PLAN prefix as a product, never EXECUTE) |
| **EXECUTE** (do) | Attempt one method, judge against its criterion | `hermes -z` oneshot via `oneshot.py` | plain task prompt, code-only verdict from `result-<id>.json` |
| **EXPLORE** (alternatives) | One method failed — search the method space | method-explorer (fka resilient-planner) | scored frontier + dead set; the only skill allowed to burn ticks trying alternatives |
| **VERIFY** (confirm) | Is done *actually* done, with receipts? | **nobody yet** — deferred | today: task self-judge + final intent-verification task + report rollup; the checker that writes ✓ receipts into dod.md is the deferred role |
| **SUBSTRATE** (durability) | Never lose progress | resumable-script | replay+memoize engine, domain-blind |

Naming rule this table enforces: a skill's name states its ROLE, not its
qualities. That is why `resilient-planner` became `method-explorer` (PLAN
belongs to task-decomposer; its role is EXPLORE) and `information-gain` became
`next-best-questions` earlier. `relentless-solve` keeps its brand name because
it is the user-facing entry point and the name states the promise.

## State contracts

Two species. **Stateless transformers** reconstruct everything from their
inputs each call; **state owners** hold a durable store they read+write across
invocations. House rule: **no store has two writers.**

| Skill | State IN | Does | State OUT | Owns durable state? |
|---|---|---|---|---|
| define-done | intent text (+ existing dod.md for amendments) | one authoring pass | `specs/<slug>/dod.md` — STATE header, R-groups, markers `○ ✓ ~`, checks, OPEN | No — authors it, never revisits (runtime write authority = the VERIFY gap) |
| next-best-questions | problem + posterior/evidence | rank questions by √(U·EVSI) | ranked questions | No |
| investigator | problem + seed evidence (ledger receipts as tombstones) | rank → answer top-K via grounded agents → distill | fresh facts appended to `<run_dir>/tombstones.jsonl` + `answer-<fp>.json`; `stop_reason` | **Yes** — its run-dir journal (artifact-based resume) |
| task-decomposer | intent + facts/dead-fps/gaps + unmet R-ids + forbidden ids | one oneshot, validate, retry-echo ≤3 | `c<N>/plan.json` (disposition: tasks \| needs_decision \| exhausted) | No — pure function of its prompt |
| — its `report.py` | plan + results + parsed dod + knowledge_in fps | pure fold + rollup | `c<N>/report.json` {status, tasks, requirements, delta} | No (relentless invokes and persists) |
| relentless-solve | intent (+ dod text, frozen into engine input) + prior slug state | the cycle loop, LEVELs 0/1/2 | `ledger.jsonl` (append-only), per-cycle artifacts, `report.md`, `gate.json`; terminal: success \| information-dry \| caps \| needs_decision-suspend | **Yes** — `$HERMES_HOME/relentless/<slug>/` is THE run state |
| — its `knowledge.py` | a finished run's ledger + project key | flock-guarded fp-deduped append; same-project recent-N reads | `${HERMES_HOME}/knowledge/global.jsonl` | **Yes** — the global tier's ONLY writer (house rule holds) |
| method-explorer | envelope prompt + dead set (resume suffix) | scored-frontier search over ticks | `plans/<slug>/plan-tree.md` (markers `○ ▶ ✝ ✓`, FRONTIER) + journal ticks; exit status | **Yes** — plan-tree is the search state; drive.py never writes it |
| resumable-script | flow fn + input + journal | replay memoized prefix, run live tail, suspend on ask | `flow/journal.jsonl`, `state.json` | Yes, on the flow's behalf — domain-blind |
| oneshot.py / ask | prompt + timeout / alias + question | one model invocation | CompletedProcess / answer | No — pure transport |

The key handoff: **every cycle, relentless serializes its state (ledger + dead
fps + unmet R-ids) into prompt sections for the stateless transformers, and
folds their artifacts back into the ledger.** State never flows between
transformers directly. (`plans/<slug>` keeps its historical dir name — renaming
a state-dir convention would break resumability for zero clarity gain.)

## Call graph

```
DIRECT — the dev loop (jim's; a human or his script — NEVER a family component)
 ├─ scope ─── CLARIFY→PLAN rounds only → scope package (scope.md + scope.json)
 │            read-only research in a disposable sibling worktree of --answer-cwd
 └─ relentless-solve  «solve» gate (1 LLM call → gate.json) → route
     ├─ trivial ────────── oneshot.py → hermes -z (one pass-through)
     ├─ single_method ──── method-explorer envelope → drive.py ticks → hermes -z
     └─ full ───────────── resumable-script engine (flow journal, v4)
          per cycle c<N>:
          ├─ CLARIFY (L0) … investigator.iterate → nbq ranker → answerer → ask.model_utils
          ├─ PLAN ………………… task-decomposer envelope → oneshot → plan.json (validate/coverage/dead)
          ├─ EXECUTE (L1) … run_intent_path → per task (L2) run_task_with_local_retry
          │                   ├─ oneshot.py → hermes -z (plain prompt, self-judged)
          │                   ├─ scoped clarify (investigator, task-scoped)
          │                   └─ delegation ≤1/cycle → method-explorer drive.py (small caps)
          ├─ HARVEST ……………… harvest.py fold → ledger.jsonl
          └─ REPORT ……………… task-decomposer report.py → c<N>/report.json (+ rollup in report.md)

 define-done runs UPSTREAM (intent → dod.md); relentless --dod parses it via _spec()
```

- **One layer above, nothing calls upward — mechanically.** Every relentless entry
  sets `RELENTLESS_ACTIVE=<slug>`; executors/answerers inherit it and a nested
  invocation exits 4 (`--allow-nested` = the explicit escape). EXECUTE stays
  **pluggable** (a task is atomic in CONTRACT — one method, one criterion, one
  `result-<id>.json` — not in execution; any richer dev-loop harness can be the
  executor), and an oversized atom decomposes **through MAP, never through nested
  ORCHESTRATE**: verdict `needs_split` (+ `split` list) skips local retry and
  delegation and forces an immediate partial replan with the SPLIT HINT folded as a
  fact. Cross-run continuity lives in the knowledge plane, not in any loop's memory.
- **Acyclic, orchestrator-at-top (within the family).** relentless imports everyone via lazy
  loaders (env override → same-repo sibling → deployed `$HERMES_HOME`:
  `TASK_DECOMPOSER_DIR`, `DEFINE_DONE_DIR`, `METHOD_EXPLORER_DIR` — old
  `RESILIENT_ENVELOPE_DIR`/`RESILIENT_DRIVE` still accepted —
  `INVESTIGATOR_SCRIPTS_DIR`, `RESUMABLE_ENGINE_DIR`). Nothing imports
  relentless. investigator → next-best-questions → ask is the only other
  import chain. task-decomposer, define-done, method-explorer import no sibling.
- **Every cross-skill seam is either an artifact on disk or a contract-pinned
  import** (EnvelopeContract, PlanContract, InvestigatorContract,
  HarvestContract, DefineDoneContract, engine contract, oneshot contract). No
  skill is a hard dependency — each seam degrades (missing method-explorer →
  partial-replan fallback; missing dod → dark).
- Both drivers (relentless, drive.py) share `oneshot.py`; both treat on-disk
  artifacts as truth over stdout.

## Cross-cutting planes (named, not extracted)

Two planes are real but deliberately NOT skills. Each has a written trigger for
when it graduates.

1. **Knowledge plane** — `ledger.jsonl` (relentless writes via the harvest
   fold) → `c<N>/report.json` deltas (computed by task-decomposer `report.py`,
   the **schema authority**) → `${HERMES_HOME}/knowledge/global.jsonl`.
   **The extraction trigger FIRED (2026-07-03, topology B):** with a dev loop
   making many narrow relentless calls, per-slug ledgers re-learned the same
   facts, so the global tier gained its writer. Owner:
   `relentless-solve/scripts/knowledge.py` (resident there until a SECOND
   consumer appears — then it graduates to its own skill dir). Policy, pinned
   by its tests: **promotion** — at run end, `fact` + `dead-end` records only,
   flock-guarded, fp-deduped, tagged with the repo identity of `answer_cwd`
   (git common-dir realpath, so all worktrees of one repo share a key);
   **seeding** — a new run's first clarify gets the most recent same-project
   records as provenance-prefixed EVIDENCE ONLY (never folded into the ledger:
   a prior run's dead-end must never become a binding `dead_fp` elsewhere;
   null-project records never seed; no cross-project seeding); `--knowledge
   off` = hermetic. Record shape still pinned by HarvestContract;
   `relentless/harvest.py` stays a deliberate copy of the canonical fold.

2. **Invocation plane** — `oneshot.py` (bare single-turn `hermes -z`, direct
   vs docker-exec, timeout→rc-124 tolerance, artifacts-beat-stdout) + ask's
   `model_utils` (full agent chat, used by investigator/nbq). `oneshot.py`
   physically lives in resumable-script/scripts/ for deployment convenience;
   it is transport, not durability. Its API is pinned by
   relentless-solve/tests/test_oneshot_contract.py.
   **Extraction trigger:** the next time oneshot.py itself must grow (new
   transport, provider routing) — move it to its own home in that same pass.

## Regimes — staging vs deployed

Staging (`src/hermes/skills/`, not a git repo) carries: define-done,
method-explorer, relentless-solve, resumable-script, task-decomposer — synced
outward to the deployed trees (`~/.hermes/skills/` primary, profiles,
hermes-agent mirror) by rsync, staging is the source of truth.

The CLARIFY trio (investigator, next-best-questions, ask) lives **only** in
deployed trees (`~/.hermes/skills/autonomous-ai-agents/`, `productivity/`).
This divergence is **intentional** — do not "fix" it by copying them into
staging; their source of truth is the deployed/hermes-agent tree.

## Write-contract enforcement — the isolation layers

Investigating and implementing are the same MECHANISM (agent turns); the
difference is the write contract, and its enforcement is layered, stated
honestly (no absolute guarantee is claimed): the `read` capability directive
(instructional) · toolsets minus terminal (mechanical) · the answerer's
subprocess cwd pinned to a **disposable sibling worktree** created FROM the
caller's own worktree at its HEAD, uncommitted tracked changes carried across
(mechanical; scope mode) · per-round `git status` dirty receipts vs the
post-setup baseline (detection — violations land in the scope package and the
worktree is KEPT as evidence) · the container boundary (outer wall). The
containment layer (scope mode) hardens this further: content-sensitive
receipts (porcelain + a baseline-commit diff hash + HEAD, not porcelain alone,
so a same-shape edit or a hidden commit still trips detection), evidence
archived BEFORE any destructive reset, reset-to-the-baseline-commit between
rounds, tainted-round facts excluded from planning/clarify/promotion, and a
fail-closed setup (any setup step failing tears down the partial worktree
rather than leaving isolation half-built).

## Deferred (recorded, not started)

Checker/✓-receipt authority for dod.md (fills the VERIFY role) ·
sibling-worktree isolation for solve EXECUTE (designed — same pattern scope
uses, plus branch handoff of task artifacts — not built) · OPEN:→clarify
seeding beyond scope mode (scope's dod OPEN line already seeds; the solve loop
doesn't yet) · router sizing from spec shape · physical relocation of
oneshot.py · promotion of knowledge.py to its own skill (trigger: a second
consumer).
