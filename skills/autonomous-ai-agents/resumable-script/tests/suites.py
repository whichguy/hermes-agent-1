"""Named test suites — pure indexes into the rungs of BOTH ladders, by rung name.

A suite is nothing but an ORDERED LIST OF EXISTING RUNG NAMES. This file defines zero test
bodies of its own — every name here must already exist as a rung in `tests/run_ladder.py`'s
`RUNGS` list OR `tests/run_call_ladder.py`'s (the library-API ladder; its rungs take and ignore
the harness arg, so run_ladder's runner drives both uniformly). `--list-suites` validates every
name against the union of both registries — and that the two ladders never define the same
name — failing loud on drift (rename/removal). The same rung may appear in as many suites as
it's genuinely relevant to: e.g. `inresolve` sits in `engine`, `failure-policy`, and `smoke`
all at once because it really does exercise all three concerns. Adding a suite never duplicates
a test; it only groups references to ones that already exist.

There are TWO kinds of suite: FEATURE suites (which area of the system — smoke/engine/workflow/…)
and COMPLEXITY TIERS (how deep — tier1..tier5, escalating from the simplest mechanics to failure/
corruption resilience; DISJOINT, so a full climb runs each rung once). `TIERS` below is the single
source of truth for climb order; `tests/run_tiers.py` climbs it and stops at the first failure,
writing a machine-readable scoreboard to `tests/.last_run.json` (the ground-truth artifact an
ask-dispatched agent run is judged by). Run one with:
  python3 tests/run_ladder.py --suite tier1-basics
  python3 tests/run_tiers.py                          # climb all tiers, simplest first
  python3 tests/run_ladder.py --list-suites           # list suites + validate every name
"""

# The escalation order for tests/run_tiers.py — tier suites only, simplest first, disjoint.
TIERS = ["tier1-basics", "tier2-routing", "tier3-interrupts", "tier4-composition",
         "tier5-resilience"]

SUITES = {
    # ---- COMPLEXITY TIERS (escalating; disjoint; climbed in TIERS order) ----------------------
    # tier1: the spine — linear run, memoization, one gate suspend/resume, the two state
    # channels, sequential fall-through. If these fail, nothing else matters.
    "tier1-basics": ["l00", "l01", "l02", "lstate", "wf_state", "wf_seq"],
    # tier2: edges — mechanical rails (incl. ranges), interpolation across resume, the
    # self-declared-outcome fast path, human-gate routing, the judge fallback.
    "tier2-routing": ["l05", "wf_route", "wf_paths", "wf_return", "wf_decide", "wf_router"],
    # tier3: interruption + loops + conversation — multi-gate chains, the interpreter hook,
    # ASK-line continuation (single + multi-round), the scaffold contract, shared threads with
    # gates-as-turns, revision cycles bounded by max_visits.
    "tier3-interrupts": ["l03", "l04", "l10", "wf_intervene", "wf_intervene_multi",
                         "wf_scaffold", "wf_context", "wf_cycle"],
    # tier4: composition — map fan-out (incl. suspend-inside-map), search, the workflow-under-
    # ctx.call pin, nested library API.
    "tier4-composition": ["wf_map", "wf_mapsusp", "wf_search",
                          "call_wf_child", "rf_2level", "rf_3level"],
    # tier5: resilience — retries, corruption/locks, in-doubt resolution, load rejections,
    # declarative failure routing, crash-window escalation, in-hash edit-while-parked, and the
    # flagship composed investigation.
    "tier5-resilience": ["l07", "l11", "inresolve", "wf_specbad", "wf_onerr_match",
                         "wf_nonidem", "l_inhash", "wf_rehash", "inv_full"],

    # The smallest set of rungs that together touch every major feature area at least once:
    # replay/memoization, blob spill, corruption guards (torn-tail/collision/nondeterminism/
    # lock-busy in one rung via l11), headless auto-answer, flow_hash-change acceptance,
    # adjudicator hook, interpreter hook, observer hook, lock contention, --no-strict,
    # ctx.now/random/uuid, state.json correctness, in-doubt resolve verbs; on the workflow
    # side: state channels, structured-return repair, interpolation, the `agent` kind + live
    # MCP state, multi-round reentrancy, map/reduce, spec-validation-at-load, the on_error/
    # on_item_error/on_exhausted/idempotent failure-policy layer, the no-route human-gate
    # fallback, conversational context; and the flagship composed investigation example.
    # ~27 of the full ladder. Run this for a fast breadth check, not full coverage.
    "smoke": [
        "l11", "l12", "lauto", "e6", "l10", "e4", "obs", "g1", "g5", "lhelpers", "lstate",
        "inresolve",
        "wf_state", "wf_return", "wf_paths", "wf_intervene_multi",
        "wf_map", "wf_specbad", "wf_onerr_match", "wf_map_itemerr", "wf_nonidem",
        "wf_seq", "wf_cycle", "wf_context", "wf_scaffold", "wf_router", "call_wf_child", "wf_rehash",
        "inv_full",
    ],

    # The code-first substrate (scripts/engine.py) alone: hand-written ctx.step/ctx.ask flows,
    # no workflow spec involved.
    "engine": [
        "l00", "l01", "l02", "l03", "l04", "l05", "l06", "l07", "l08", "l09", "l10", "l11",
        "l12", "l13_onfail", "lprop", "lvalues", "lauto", "lidem", "lhelpers", "lfailmeta",
        "loutfile", "loutfile_badpath",
        "e1", "e2", "e3", "e4", "e5", "e6", "lstate",
        "g1", "g2", "g3", "g4", "g5",
        "d1", "d2", "d3", "d4", "d5",
        "r1", "r2", "r3", "r4", "r5", "r6",
        "inresolve", "inoptions", "obs", "l_inhash",
    ],

    # The declarative workflow spec layer (scripts/workflow.py) alone: kinds, routing,
    # interpolation, spec validation.
    "workflow": [
        "wf_state", "wf_route", "wf_return", "wf_context", "wf_decide", "wf_abort",
        "wf_intervene", "wf_intervene_multi", "wf_paths", "wf_search", "wf_map", "wf_mapsusp",
        "wf_mapedge", "wf_mapbad", "wf_specbad", "wf_seq", "wf_cycle", "wf_scaffold", "wf_router",
        "wf_onerr_route", "wf_onerr_match", "wf_onerr_replay", "wf_onerr_fallback",
        "wf_map_itemerr", "wf_map_itemerr_replay", "wf_nonidem",
    ],

    # The flagship composed example (a durable codebase investigation), exercising engine +
    # workflow mechanisms together end to end rather than in isolation.
    "investigation": [
        "inv_fix_resume", "inv_map_memo", "inv_focus_gate", "inv_apply_crash",
        "inv_report_branch", "inv_full", "inv_flaky_retry",
    ],

    # Declarative failure routing specifically -- on_fail / on_error / on_item_error /
    # idempotent -- at both the engine primitive and the workflow-spec layer.
    # Deliberately overlaps "engine" and "workflow": this is a cross-cutting concern, not a
    # separate architectural layer, and the overlap is the point (see module docstring).
    "failure-policy": [
        "l07", "l08", "l13_onfail", "lfailmeta", "inresolve", "inoptions", "r1",
        "wf_specbad",
        "wf_onerr_route", "wf_onerr_match", "wf_onerr_replay", "wf_onerr_fallback",
        "wf_map_itemerr", "wf_map_itemerr_replay", "wf_nonidem",
    ],

    # Suspend/resume + the LLM-interrupt/reentrant-prompt mechanism specifically. Also
    # cross-cutting: shares rungs with "engine", "workflow", and "smoke" on purpose.
    "reentrancy": [
        "l02", "l03", "l04", "l10", "lhelpers",
        "wf_decide", "wf_paths", "wf_context", "wf_intervene", "wf_intervene_multi", "wf_rehash",
    ],

    # ctx.call — independent, reusable CHILD flows (as opposed to the namespaced-key composition
    # "reentrancy" above already covers), across BOTH surfaces: the CLI/FileStore-backed call_*
    # rungs (tests/run_ladder.py) and the run_flow/resume_flow/export_portable_state library-API
    # rf_* rungs (tests/run_call_ladder.py — see references/nested-flows.md).
    "nested-call": [
        "call_wf_child", "call_cli_2level", "call_cli_3level", "call_crashboundary",
        "call_collision", "call_memo_strict_gap",
        "call_key_target", "call_auto_nested", "call_statejson",
        "rf_2level", "rf_3level", "rf_failed_child", "rf_in_doubt_nested",
        "rf_crash_toplevel", "call_export_hybrid",
        "rf_headless_nested", "rf_child_adjudicator", "rf_child_corruption",
        "rf_derive_status_latest", "rf_resolve_key_nested",
        "rf_child_retry_catch", "rf_child_blob", "rf_sibling_calls",
        "rf_child_runid_wait", "rf_child_interpreter", "rf_derive_drift",
        "rf_deep_chain", "rf_call_observer", "rf_nostrict_nested",
    ],
}

# TIERS invariants, enforced at import so drift fails loudly instead of relying on the docstring:
# (1) TIERS names exactly the tier* suites; (2) a full climb is DISJOINT — every rung runs once.
_tier_keys = {k for k in SUITES if k.startswith("tier")}
assert set(TIERS) == _tier_keys, (
    "TIERS must list exactly the tier* suites; drift: %s" % (set(TIERS) ^ _tier_keys))
_climb = [rung for t in TIERS for rung in SUITES[t]]
assert len(set(_climb)) == len(_climb), (
    "tiers overlap — a full climb would run a rung twice: %s"
    % sorted({r for r in _climb if _climb.count(r) > 1}))
