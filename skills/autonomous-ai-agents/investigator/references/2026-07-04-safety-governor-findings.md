# Findings: the operational-safety governor (2026-07-04)

## Thesis

The investigator's answerer spawned a `hermes chat --yolo` sub-agent for every research
activity, at maximum privilege (`file,web,terminal`), unattended, with no reversibility gate,
no backups, and no destructive-op check — the pre-existing `experiment` capability tier was
advisory-only (identical toolset to `act`, differing only by a prompt sentence). Built a
least-privilege escalation ladder instead: classify each pending activity
(READ_ONLY/SANDBOX/MODIFY/DESTRUCTIVE) via an independent second-opinion model call, clamp by
the `--capability` ceiling, and map the effective tier to a real, enforced dispatch posture.

## What was built

- **`scripts/opclass.py`** — `classify_operation()`, a direct-Ollama classifier (mirrors the
  `productivity/triage` skill's micro-pattern) that is **fail-safe**, not fail-open: any
  network error, timeout, malformed JSON, missing/invalid field funnels into
  `DESTRUCTIVE + irreversible=True + touches_irreplaceable=True + needs_confirmation=True`.
  Never the permissive default.
- **The escalation ladder** (`answerer.py`'s `_safety_posture()`, replacing the old static
  `apply_capability()` toolset lookup): per-activity tier decision, ceiling-clamped
  (`experiment` caps at SANDBOX, `read` caps at READ_ONLY, clamp happens *before* the
  DESTRUCTIVE-specific gate so a restrictive ceiling neutralizes risk before irreversibility
  even needs to be consulted). READ_ONLY genuinely strips `terminal` from the dispatched
  toolset (the enforcement the old `experiment` tier lacked). DESTRUCTIVE + irreversible +
  no confirmer (the headless default) **hard-blocks — no sub-agent is ever dispatched.**
- **`scripts/backup.py`** — snapshot-before-modify with a colocated `manifest.jsonl`, so backups
  are self-locating without needing to thread metadata through `grounded_answer`'s 2-tuple
  return contract.
- **A supporting cross-skill change**: `dispatch_single(..., yolo: bool = True)` in
  `~/.hermes/skills/productivity/ask/scripts/model_utils.py` (canonical source: the `~/.hermes`
  repo itself) — purely additive, every existing caller unaffected, needed because the shared
  "ask" skill hardcoded `--yolo` with no toggle at all. Confirmed via grep that no existing
  caller passes `yolo=`.
- **The iCloud/location plan-review fixture** (`tests/test_icloud_location_fixture.py`) — the
  concrete design test case, tying together read-only Find-My research, a feasibility-gap
  annotation (no real iCloud location API exists), and the never-delete-proprietary-content
  rule (a delete-photos sub-op regression-tests to DESTRUCTIVE+irreplaceable → blocked, even
  under the most permissive `act` ceiling; the same op under `experiment` ceiling correctly
  sandboxes instead of blocking — both are intentional, different tiers of the same design).

## Independent review before implementation

Design decisions (the ceiling-clamp-before-gate ordering, the `yolo` param's minimal-diff
scope, the READ_ONLY/SANDBOX/MODIFY/DESTRUCTIVE posture mapping) were pressure-tested by a
`Plan`-type review agent before coding, and the resulting code + this plan were then reviewed
by an independent 2-model advisor panel (`kimi-k2.7-code:cloud`, `qwen3.6:35b-a3b`, via
Hermes's `advisors` skill) reading the actual source. **Both seats independently converged on
the same critical gap, unprompted:** every test of the MODIFY and SANDBOX tiers mocked the
thing that actually mattered — `backup.snapshot`'s real disk copy and `_prepare_sandbox`'s real
`git worktree add` path had never executed, even in a unit test. That review directly produced
the two new real-path tests below, plus the live MODIFY-tier check.

## What was closed as a result

- **Real git-worktree test**: `git init`s an actual temp repo, calls `_prepare_sandbox()`
  unmocked, asserts the returned path shows up in `git worktree list` run against the real
  repo — proving the git-worktree path executes, not just the `shutil.copytree` fallback that
  every prior test (using a non-git temp dir) silently fell through to.
- **Real backup integration test**: exercises `grounded_answer()` → `_safety_posture()` →
  `backup.snapshot()` with only `classify_operation`/`dispatch_single` mocked — `backup.snapshot`
  itself runs for real, byte-identical copy + manifest entry verified.
- **A cheap bundled fix**: `_sandbox_path()`'s naming used second-granularity timestamps (two
  questions dispatched in the same second would collide); added a uuid suffix.
- **The anti-fabrication fix** (`_NO_TOOLS_NOTE`, shared with the companion stakes-aware-respond
  investigation — see that worktree's findings doc for the full story): this project's own live
  smoke test independently hit the same fabrication bug Thread A found (a plan-review response
  claimed "I ran the actual authentication flow... error code -20283" from a toolless call, with
  zero backing evidence) — proving the bug is general to `respond()`'s toolless dispatch, not
  specific to any one feature. Applied here too, to all four toolless dispatch functions.

Full suite: 165/165 passing (157→165 across this session's work, zero regressions at each step).

## The one thing that's still an open question: `yolo=False` in a headless container

Both advisor seats also flagged, independently, that nobody had verified what `yolo=False`
actually does when `dispatch_single` runs with no interactive approver present — the original
design doc's own "Open Unknown — verify at implementation" was never actually checked. It was
checked here, deliberately: a live run forced a MODIFY verdict (mocked `classify_operation`
only) against a real scratch git repo, with a real `dispatch_single`/`hermes chat` call and
`yolo=False`.

**Result: it timed out.** The dispatched sub-agent did not execute the requested file edit and
did not return a clear denial message — it ran for the entire configured timeout (60s in this
diagnostic run; production default is 300s) before `dispatch_single`'s own subprocess timeout
killed it and returned a synthetic error. The target file was confirmed unchanged (ground truth,
checked independently of what any response claimed). A backup was still correctly taken before
the attempt, harmlessly. The container's `config.yaml` has `approvals.mode: auto` (not a value
`approval.py` actually recognizes — falls back to `manual` with a warning) and `cron_mode:
allow`, but the live check could not determine whether that misconfiguration explains the
timeout, or whether the model/agent loop was simply slow for unrelated reasons — it confirmed
*that* it hangs-to-timeout, honestly, without overclaiming a root cause.

**Practical implication:** MODIFY tier is *safe* (nothing gets modified without authorization,
by construction — backup still happens, timeout still fires) but is **not currently reliable**
in this deployment: every MODIFY-classified activity in a real investigation will burn the full
`answer_timeout` (5 minutes by default) before failing. This needs its own follow-up decision —
options include diagnosing the exact approval-path branch being hit, shortening
`answer_timeout` specifically for MODIFY-tier dispatches, or reconsidering whether MODIFY should
default to something else (e.g. always-SANDBOX) in a headless run — none of which were
undertaken here per the explicit "report it, don't route around it in this pass" instruction
this investigation was run under.

## Verdict

The governor's design and enforcement are sound and now have real (not just mocked) test
coverage for every code path except one. READ_ONLY and the DESTRUCTIVE block are both
live-verified end-to-end with a real, non-mocked classifier call. MODIFY's *safety* is
confirmed; its *reliability* in this exact headless environment is not, and is the one
unresolved item carried forward.
