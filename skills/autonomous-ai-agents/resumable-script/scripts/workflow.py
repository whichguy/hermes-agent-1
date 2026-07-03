#!/usr/bin/env python3
"""workflow — a data-defined, durable, LLM-authored WORKFLOW engine (Python reference).

A workflow is a JSON state machine (`{id, version, start, states}`). A small interpreter
compiles it onto the resumable-script engine, so an author (human OR an LLM)
writes *prompts + routing + a function registry*, not control flow. The walk is a pure
function of journaled step/ask results, so it is durable and deterministic on replay for
free — the engine memoizes every model call and human answer.

  load_workflow(spec, registry, llm=, search=) -> Flow   # a Flow the engine can run

Kinds: `run` (registry fn) · `prompt` (one llm call) · `search` (injected web caller -> structured
results, routable via `$.<step>.results[..]`) · `map` (sequential map-reduce over a `$`-path list)
· `ask` (human input: the human produces the step's value, mirror of `prompt`; RENAMED from
`decide`). A `prompt` step may interrupt (an `ASK:` reply) -> a durable human gate whose answer
enriches the conversation -> the step is re-called. See references/workflow.md.

TWO STATE CHANNELS (grep "wf:"):
  - flowing pipe   : a step's `result` is the next step's input by default (`${in}`).
  - named global   : one JSON object; each step's result auto-stored under `$.<id>`;
                     reads via a JSONPath SUBSET (dot/index), writes via set/append/delete.
INTERPOLATION       : ONE `${...}` engine for every string value (prompt/ask templates +
                     mutation sources): `${$.path}` (global), `${@.path}` (this step's return),
                     `${in}` (flowing input); `$${` escapes; a lone `${...}` keeps native type;
                     missing -> "". Bare `$.path` is only for STRUCTURAL spots (mutation target
                     keys, `when` predicates). Spec is JSON (canonical) or YAML (read_spec).
MODEL STEP (v2)    : the TASK call runs the author's directive (pure user message under an
                     engine-owned system prefix) and replies with ONE JSON object (tolerantly
                     extracted; bounded repair) or an `ASK:` line (-> durable human gate -> the
                     answer is woven back and the task re-attempts). A separate ROUTER call (an
                     independent judge; `router=`, defaults to llm) picks the edge when `routes`
                     exist — binding by default, `proceed` only on `"optional": true` steps,
                     `ask` = the reasoned can't-route path. Writes are AUTHORED-ONLY (`set` over
                     `${@...}`).
ROUTING            : `when` predicates first (`eq`/`ne` comparisons; total, never throw), then the
                     emitted `next` label through `routes` (binding; an unmapped label falls onward
                     only on `"optional": true` steps), then the declared `next`, then SEQUENTIAL
                     FALL-THROUGH (next state in declaration order; last -> @done). Terminals
                     `@done` / `@fail`. Cycles are native (per-visit keys) and bounded by a fixed
                     visit-cap safety constant.
CONVERSATION        : prompt steps SHARE one flow-wide conversation by default (the workflow is one
                     context; steps are directed prompts, answers are user turns) — rebuilt from
                     journaled responses on replay. Opt OUT per step or flow-wide with
                     `context: "isolated"`; ${...} holes remain the explicit data channel either way.
"""
import hashlib
import json
import re

# Imported lazily-safe: engine.py sits next to this file on sys.path
# (engine.load_flow inserts scripts/ onto the path before importing a flow module).
from engine import Flow

MAX_REPAIR = 2          # wf: bounded repair rounds (JSON/outcome per step; the judge has its own)
MAX_INTERVENE = 3       # wf: ask/feedback rounds (enriched-context resume) before exhaustion
def _in_hash(obj):
    """Canonical input hash for a model/search call — journal metadata ("outside the prompt"): the
    memoized result is honored on replay only if the input that would be sent now is byte-identical
    (see engine.step in_hash / journal-format.md). Edits cascade: a changed upstream reply changes
    downstream rendered inputs, so their calls re-execute too. Human answers are position-keyed and
    survive."""
    return "sha256:" + hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


MAX_VISITS = 25         # wf: default per-state visit cap (spec `max_visits` overrides) — bounds
                        # model↔model cycles that carry no human gate or data predicate

# ----------------------------------------------------------------------------- engine-owned prompt scaffolding
# wf: EVERY word the engine injects into a model conversation lives HERE, in one auditable block.
# The convo shape is fixed: [system] engine prefix (framing + JSON-result rule + ASK rule + the
# outcomes block when routed) followed by [user] the author's rendered directive — PURE, never
# mutated, nothing appended. Detection contracts (the ASK: line, the one-JSON-object reply) stay
# stable because the teaching is identical on every call. The ROUTER (an independent judge) has its
# own system message; worker scaffolding and judge scaffolding never mix.
_ASK_RULE = ("If - and only if - you cannot complete this without information from a human, reply "
             "with a single line starting `ASK: ` followed by your question, and nothing else.")
_TASK_SYSTEM = ("You are executing one step of a workflow. Do exactly what the instruction asks.\n"
                "Return your result as a single JSON object. The instruction may define its shape; "
                'if it does not, reply {"result": <your output>}.\n'
                + _ASK_RULE)
# wf: the EXPECTED OUTPUT contract LEADS the system message on routed steps ("in front of the
# prompt") — the exit gate's menu is declared before the directive and the reply's `outcome` field
# is inspected MECHANICALLY (fast path, zero judge calls); the independent router is the fallback.
_OUTCOME_CONTRACT = ('EXPECTED OUTPUT - your JSON reply MUST include a field "outcome" set to '
                     "EXACTLY one of:\n%s%s"
                     "Include the evidence for your outcome in the same reply; it must stand alone "
                     "(any fallback judge sees ONLY this output, not the conversation).\n\n")
_OUTCOME_PROCEED = "- proceed: none of the above applies; continue on the default path\n"
_OUTCOME_REPAIR = ('Your reply must include "outcome": exactly one of: %s. '
                   "Reply again as exactly one JSON object.")
_HUMAN_ANSWER = "The human answered: %s. Continue the instruction with this information."
_JSON_REPAIR = ("Your reply was not a single parsable JSON object. "
                "Reply again as exactly one JSON object, no prose.")
_ROUTER_SYSTEM = ("You are the routing judge for one step of a workflow. Given the step's "
                  "instruction, its output, and the possible outcomes, decide which outcome the "
                  'output clearly satisfies. Reply with a single JSON object: {"outcome": "<label>"}. '
                  'If the output does NOT clearly determine an outcome, reply {"outcome": "ask", '
                  '"question": "<why you cannot clearly route it, and what would disambiguate>"}.'
                  "%s")   # optional-steps addendum
_ROUTER_PROCEED = (' If none of the outcomes applies, you may reply {"outcome": "proceed"} to let '
                   "the workflow continue on its default path.")
_ROUTER_USER = "INSTRUCTION:\n%s\n\nOUTPUT:\n%s\n\nPOSSIBLE OUTCOMES:\n%s"
_ROUTER_REPAIR = ("Your previous reply was invalid: %s. Reply again with exactly one JSON object "
                  "of the required form.")
_CANT_PROCEED = ("This step could not complete automatically: %s "
                 "What information or guidance should be used to finish it?")
_LONE_HOLE = re.compile(r"^\$\{([^}]*)\}$")   # a value that is exactly one ${...} (type-preserving)


# ----------------------------------------------------------------------------- JSONPath subset
# wf: dot/index only — "$.a.b[0].c", "@.x". Root is "$" (global state) or "@" (return value).
def _parse_path(path):
    if not path or path[0] not in "$@":
        raise ValueError("path must start with $ or @: %r" % path)
    root = path[0]
    rest = path[1:]
    toks = []
    j = 0
    while j < len(rest):
        ch = rest[j]
        if ch == ".":
            j += 1
            start = j
            while j < len(rest) and rest[j] not in ".[":
                j += 1
            if j > start:
                toks.append(rest[start:j])
        elif ch == "[":
            k = rest.find("]", j)
            if k < 0:
                raise ValueError("unterminated index in path %r" % path)
            toks.append(int(rest[j + 1:k]))
            j = k + 1
        else:
            raise ValueError("bad path syntax %r near %r" % (path, rest[j:]))
    return root, toks


def _get(container, toks):
    cur = container
    for t in toks:
        if isinstance(t, int):
            if not isinstance(cur, list) or t >= len(cur) or t < -len(cur):
                return None
            cur = cur[t]
        else:
            if not isinstance(cur, dict) or t not in cur:
                return None
            cur = cur[t]
    return cur


def _set_path(state, toks, value):
    # wf: intermediates auto-create OBJECTS only; an int token requires an already-present list (we never
    # auto-vivify lists). A str token requires/creates a dict. Same rules in engine.js for parity.
    if not toks:
        raise ValueError("cannot assign to bare root $")
    cur = state
    for t in toks[:-1]:
        if isinstance(t, int):
            if not isinstance(cur, list) or t >= len(cur) or t < -len(cur):
                raise ValueError("cannot set through list index %d (no such element)" % t)
            cur = cur[t]
        else:
            nxt = cur.get(t) if isinstance(cur, dict) else None
            if not isinstance(nxt, (dict, list)):     # descend into an existing container; else make a dict
                nxt = {}
                cur[t] = nxt
            cur = nxt
    last = toks[-1]
    if isinstance(last, int):
        if not isinstance(cur, list) or last >= len(cur) or last < -len(cur):
            raise ValueError("cannot set list index %d (no such element)" % last)
        cur[last] = value
    else:
        if not isinstance(cur, dict):
            raise ValueError("cannot set key %r on a non-object" % last)
        cur[last] = value


def _del_path(state, toks):
    # wf: descend through lists (int token) AND dicts, exactly like _set_path/getPath and engine.js
    # delPath — parity. Only a final DICT key is removed (neither engine deletes a final list index).
    cur = state
    for t in toks[:-1]:
        if isinstance(cur, list) and isinstance(t, int):
            if not (-len(cur) <= t < len(cur)):
                return
            cur = cur[t]
        elif isinstance(cur, dict) and t in cur:
            cur = cur[t]
        else:
            return
    if isinstance(cur, dict):
        cur.pop(toks[-1], None)


def _resolve(state, ret, root, toks):
    return _get(ret if root == "@" else state, toks)


def _stringify(value):
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(",", ":"))


# ----------------------------------------------------------------------------- interpolation (unified ${...})
# wf: ONE engine for every string VALUE (prompt/agent/ask templates + mutation sources). A hole is
# `${$.path}` (global state), `${@.path}`/`${@}` (this step's return), or `${in}` (the flowing input).
# `$${` escapes a literal `${`. Missing/None -> "". A value that is a LONE ${...} keeps its native type;
# embedded holes stringify. Bare `$.path` (no `${}`) is only for STRUCTURAL positions (mutation target
# keys, `when` predicates) — never value-inspected. See references/workflow.md.
def _resolve_hole(expr, state, flowing, ret):
    expr = expr.strip()
    if expr == "in":
        return flowing
    root, toks = _parse_path(expr)
    return _resolve(state, ret, root, toks)


def _render_template(template, state, flowing, ret=None):
    """Interpolate ${...} holes into text -> always a string. `$${` -> literal `${`; missing -> ""."""
    out = []
    i, n = 0, len(template)
    while i < n:
        if template[i] == "$":
            if template[i + 1:i + 3] == "${":              # $${  -> literal ${
                out.append("${")
                i += 3
                continue
            if i + 1 < n and template[i + 1] == "{":
                j = template.find("}", i + 2)
                if j != -1:
                    val = _resolve_hole(template[i + 2:j], state, flowing, ret)
                    out.append("" if val is None else _stringify(val))
                    i = j + 1
                    continue
        out.append(template[i])
        i += 1
    return "".join(out)


def _resolve_value(v, state, flowing, ret):
    """A mutation source value: non-string -> literal; a lone ${...} -> native type; else interpolate."""
    if not isinstance(v, str):
        return v
    m = _LONE_HOLE.match(v)
    if m:
        return _resolve_hole(m.group(1), state, flowing, ret)
    return _render_template(v, state, flowing, ret)


def _resolve_deep(v, state, flowing, ret):
    """Resolve ${...} holes RECURSIVELY through dicts/lists. Used ONLY for failure-policy `result`
    values (on_error) — a fallback result is a structured stand-in whose embedded
    holes (e.g. "${@.__error__.message}") must interpolate; `set` sources stay shallow (§6)."""
    if isinstance(v, dict):
        return {k: _resolve_deep(x, state, flowing, ret) for k, x in v.items()}
    if isinstance(v, list):
        return [_resolve_deep(x, state, flowing, ret) for x in v]
    return _resolve_value(v, state, flowing, ret)


# ----------------------------------------------------------------------------- mutations + predicates
def _mut_toks(kind, target):
    # wf: mutation targets are bare `$` paths (global state only). A non-$ root (e.g. `@.x`) is a spec
    # error — reject it loudly instead of silently writing into $. (engine.js applyOps mirrors this.)
    root, toks = _parse_path(target)
    if root != "$":
        # message kept unquoted to stay byte-identical with engine.js mutToks (cross-language parity)
        raise ValueError("%s target must write into $ (global state): %s" % (kind, target))
    return toks


def _apply_ops(state, flowing, ret, ops):
    # wf: state writes — set/append/delete. Target keys are bare `$` paths; sources are ${...} values.
    for target, source in (ops.get("set") or {}).items():
        _set_path(state, _mut_toks("set", target), _resolve_value(source, state, flowing, ret))
    for target, source in (ops.get("append") or {}).items():
        toks = _mut_toks("append", target)
        lst = _get(state, toks)
        if not isinstance(lst, list):
            lst = []
            _set_path(state, toks, lst)
        lst.append(_resolve_value(source, state, flowing, ret))
    for target in (ops.get("delete") or []):
        _del_path(state, _mut_toks("delete", target))


# wf: the dict-predicate comparison operators. TOTAL semantics — a missing path is simply
# unequal, never a throw (predicates must not be able to crash a flow).
_PRED_OPS = ("eq", "ne")


def _pred_compare(op, val, ref):
    if op == "eq":
        return val == ref
    return val != ref                        # ne


def _eval_pred(cond, registry, state, result):
    # wf: a `when` predicate — a $-path (truthy), a {path, eq|ne: <ref>} compare, or a registry fn.
    if isinstance(cond, dict):
        root, toks = _parse_path(cond["path"])
        val = _resolve(state, result, root, toks)
        for op in _PRED_OPS:
            if op in cond:
                return _pred_compare(op, val, cond[op])
        return bool(val)
    if isinstance(cond, str):
        if cond[:1] in "$@":
            root, toks = _parse_path(cond)
            return bool(_resolve(state, result, root, toks))
        fn = registry.get(cond)
        if fn is None:
            raise ValueError("unknown predicate %r (not a $-path or registry fn)" % cond)
        return bool(fn(state, result))
    raise ValueError("bad `when.if` predicate: %r" % cond)


# ----------------------------------------------------------------------------- return contract
# wf: routes grammar — shorthand `"label": "target"` OR object `"label": {"to": "target",
# "means": "<condition text>"}`. `means` is what the task and the router are SHOWN for that
# label; when absent, the label itself is the condition.
def _route_target(routes, label):
    entry = routes.get(label)
    if isinstance(entry, dict):
        return entry.get("to")
    return entry


def _route_means(routes, label):
    entry = routes.get(label)
    if isinstance(entry, dict) and entry.get("means"):
        return entry["means"]
    return label


def _outcomes_text(routes):
    return "\n".join("- %s: %s" % (label, _route_means(routes, label)) for label in routes)


# ----------------------------------------------------------------------------- spec validation
_KINDS = ("run", "prompt", "search", "map", "ask")
_SEARCH_FORMATS = ("structured",)
# wf: map is a PURE FAN-OUT — routing/mutation/context keys on its `do`/`reduce` would be silently
# dead (or worse: an inner prompt's `routes` gets ENFORCED by the return contract, then discarded).
# Fail-fast at load instead of arguing with ourselves at runtime.
_MAP_INNER_FORBIDDEN = ("routes", "when", "next", "set", "append", "delete", "context",
                        "on_error", "idempotent")
_ON_ERROR_KEYS = ("match", "retries", "backoff_ms", "to", "result")


def _kind_of(stepdef):
    present = [k for k in _KINDS if k in stepdef]
    if len(present) != 1:
        raise ValueError("a state must have exactly one kind of %s, found %s" % (list(_KINDS), present))
    return present[0]


def _check_pure_fanout(name, where, sub):
    bad = [k for k in _MAP_INNER_FORBIDDEN if k in sub]
    if bad:
        raise ValueError("state %r map `%s` cannot carry %s — map is a pure fan-out; routing and "
                         "mutations belong on the map state itself" % (name, where, "/".join(bad)))


def _check_search_format(name, sub):
    fmt = sub.get("format", "structured")
    if fmt not in _SEARCH_FORMATS:
        raise ValueError("state %r search `format` must be \"structured\" (got %s)"
                         % (name, _stringify(fmt)))


# ----------------------------------------------------------------------------- failure policy (on_error)
# wf: `on_error` is an ORDERED matcher list — a per-state decision ladder walked on each failing
# attempt. First matching rule wins fully: retries left -> retry (its backoff); else `to`/`result`
# present -> CATCH (memoize the __error__ sentinel; see engine on_fail); else -> raise (exit 1).
# A single object is sugar for a one-rule list. Keep `match` regexes to the common Python-re /
# JS-RegExp subset (alternation, classes, anchors — no lookbehind): each engine compiles at load.
def _on_error_rules(stepdef):
    oe = stepdef.get("on_error")
    if oe is None:
        return None
    return [oe] if isinstance(oe, dict) else oe


def _check_failure_policy(name, kind, stepdef, legal):
    if "on_error" in stepdef:
        if kind not in ("run", "search"):
            raise ValueError("state %r: on_error is only allowed on run/search states" % name)
        rules = _on_error_rules(stepdef)
        if not isinstance(rules, list) or not rules:
            raise ValueError("state %r: on_error needs at least one rule" % name)
        for i, rule in enumerate(rules):
            if not isinstance(rule, dict) or not rule:
                raise ValueError("state %r: on_error rule %d must be a non-empty object" % (name, i))
            bad = sorted(k for k in rule if k not in _ON_ERROR_KEYS)
            if bad:
                raise ValueError("state %r: on_error rule %d has unknown keys %s"
                                 % (name, i, "/".join(bad)))
            for k in ("retries", "backoff_ms"):
                if k in rule and (isinstance(rule[k], bool) or not isinstance(rule[k], int) or rule[k] < 0):
                    raise ValueError("state %r: on_error rule %d `%s` must be a non-negative integer"
                                     % (name, i, k))
            pat = rule.get("match", "*")
            if not isinstance(pat, str):
                raise ValueError("state %r: bad on_error match regex (must be a string)" % name)
            if pat != "*":
                # wf: `(?...` constructs beyond (?: (?= (?! diverge across regex flavors — Python
                # accepts (?i)/(?P<...> that JS rejects (and vice versa). Gate them in BOTH
                # engines so a spec is loadable in one iff loadable in the other.
                if re.search(r"\(\?[^:=!]", pat):
                    raise ValueError("state %r: bad on_error match regex %s (only (?: (?= (?! "
                                     "groups are portable across engines)" % (name, _stringify(pat)))
                try:
                    re.compile(pat, re.ASCII)
                except re.error as e:
                    raise ValueError("state %r: bad on_error match regex %s (%s)"
                                     % (name, _stringify(pat), e))
            if "to" in rule and rule["to"] not in legal:
                raise ValueError("state %r: on_error `to` routes to unknown target %r"
                                 % (name, rule["to"]))
    if "idempotent" in stepdef:
        if kind != "run":
            raise ValueError("state %r: `idempotent` is only allowed on run states" % name)
        if not isinstance(stepdef["idempotent"], bool):
            raise ValueError("state %r: `idempotent` must be a boolean" % name)


def _match_rule(rules, err):
    # First rule whose `match` regex (searched against "<name>: <message>") matches; "*"/absent
    # matches all. Used live (via _compile_on_fail) AND on replay to re-derive `to`/`result` from
    # a memoized sentinel — same rules + same error => same rule => same branch.
    # CROSS-ENGINE SEMANTICS (must mirror workflow.js matchRule exactly):
    #   - trailing newlines are stripped from the haystack (Python `$` matches before a trailing
    #     \n, JS `$` does not — stripping makes both mean plain end-of-string);
    #   - re.ASCII pins \d/\w/\s/\b to ASCII (JS RegExp classes are ASCII by default).
    hay = ("%s: %s" % (err.get("name", ""), err.get("message", ""))).rstrip("\r\n")
    for rule in rules:
        pat = rule.get("match", "*")
        if pat == "*" or re.search(pat, hay, re.ASCII):
            return rule
    return None


def _compile_on_fail(rules):
    # Compile the rule list into the engine's per-attempt on_fail hook. Per-rule retry counters
    # are per PASS (retries happen within one invocation; only the terminal outcome is journaled).
    counts = {}
    def on_fail(err, attempt):
        rule = _match_rule(rules, err)
        if rule is None:
            return {"action": "raise"}
        idx = rules.index(rule)
        if counts.get(idx, 0) < rule.get("retries", 0):
            counts[idx] = counts.get(idx, 0) + 1
            return {"action": "retry", "backoff_ms": rule.get("backoff_ms", 0)}
        if "to" in rule or "result" in rule:
            return {"action": "catch"}
        return {"action": "raise"}
    return on_fail


def _is_error_sentinel(value):
    return isinstance(value, dict) and isinstance(value.get("__error__"), dict)


def _item_policy(retries, backoff_ms, catch):
    # Per-map-item on_fail: a fresh counter per item; retry up to `retries`, then catch (collect/
    # skip) or raise (fail mode with retry knobs).
    used = {"n": 0}
    def on_fail(err, attempt):
        if used["n"] < retries:
            used["n"] += 1
            return {"action": "retry", "backoff_ms": backoff_ms}
        return {"action": "catch"} if catch else {"action": "raise"}
    return on_fail


def _check_when(name, stepdef):
    # wf: dict predicates carry AT MOST ONE comparison operator, and nothing but `path` + operators —
    # an unknown key (typo'd op) must fail at load, not silently truthiness-match at runtime.
    for cond in stepdef.get("when", []):
        pred = cond.get("if") if isinstance(cond, dict) else None
        if not isinstance(pred, dict):
            continue
        if "path" not in pred:
            raise ValueError("state %r `when` dict predicate needs `path`" % name)
        ops = [k for k in pred if k != "path"]
        unknown = [k for k in ops if k not in _PRED_OPS]
        if unknown:
            raise ValueError("state %r `when` predicate has unknown key(s) %s (operators: %s)"
                             % (name, unknown, "/".join(_PRED_OPS)))
        if len(ops) > 1:
            raise ValueError("state %r `when` predicate must use exactly one operator (got %s)"
                             % (name, ops))


def _validate_spec(spec, registry):
    if not isinstance(spec, dict) or "states" not in spec or "start" not in spec:
        raise ValueError("workflow spec needs `start` and `states`")
    states = spec["states"]
    legal = set(states) | {"@done", "@fail"}
    reserved = set(states) | {"input"}
    if spec["start"] not in states:
        raise ValueError("start state %r is not defined" % spec["start"])
    if "namespace" in spec:
        raise ValueError("`namespace` is no longer a supported spec key")
    if "max_visits" in spec:
        raise ValueError("`max_visits` is no longer a supported spec key "
                         "(the visit cap is a fixed safety constant)")
    for name, stepdef in states.items():
        if "on_exhausted" in stepdef:
            raise ValueError("state %r: on_exhausted is no longer supported" % name)
        kind = _kind_of(stepdef)
        _check_failure_policy(name, kind, stepdef, legal)
        _check_when(name, stepdef)
        if "optional" in stepdef and not isinstance(stepdef["optional"], bool):
            raise ValueError("state %r `optional` must be a boolean" % name)
        if "context" in stepdef and stepdef["context"] not in ("shared", "isolated"):
            raise ValueError("state %r `context` must be \"shared\" or \"isolated\" (got %s)"
                             % (name, _stringify(stepdef["context"])))
        if kind in ("prompt", "ask") and stepdef.get("routes") \
                and "next" in stepdef and not stepdef.get("optional"):
            # wf: routes are BINDING — a declared `next` on a strict routed step is unreachable dead
            # spec (only `"optional": true` can fall past the menu). Fail loud at load.
            raise ValueError("state %r declares binding `routes` AND `next` — `next` is unreachable "
                             "(add \"optional\": true or remove `next`)" % name)
        if kind == "ask" and stepdef.get("options") and stepdef.get("routes") \
                and not stepdef.get("optional"):
            # wf: routes are BINDING — every option a human can pick must map somewhere, checked
            # statically. `"optional": true` relaxes this (an unmapped answer falls onward).
            unmapped = [o for o in stepdef["options"] if o not in stepdef["routes"]]
            if unmapped:
                raise ValueError("state %r `ask` options %s are not mapped in `routes` "
                                 "(map them, or set \"optional\": true to fall through)"
                                 % (name, unmapped))
        if kind in ("run",) and stepdef[kind] not in registry:
            raise ValueError("state %r references unknown registry fn %r" % (name, stepdef[kind]))
        if kind == "search":
            _check_search_format(name, stepdef)
        if kind == "map":
            mapspec = stepdef["map"]
            if "over" not in mapspec or "do" not in mapspec:
                raise ValueError("state %r map needs `over` and `do`" % name)
            as_name = mapspec.get("as", "it")
            # wf: $.<as> / $.<as>_index shadow state names inside the map — a collision would silently
            # repoint ${$.input...} (or any prior step's result). Reject at load.
            if as_name in reserved or (as_name + "_index") in reserved:
                raise ValueError("state %r map `as` %r collides with `input` or a state name"
                                 % (name, as_name))
            inner = mapspec["do"]
            ikind = _kind_of(inner)
            if ikind == "map":
                raise ValueError("state %r map `do` cannot itself be a map" % name)
            if ikind == "run" and inner["run"] not in registry:
                raise ValueError("state %r map `do` references unknown registry fn %r" % (name, inner["run"]))
            if ikind == "search":
                _check_search_format(name, inner)
            _check_pure_fanout(name, "do", inner)
            # wf: per-item failure policy — mode + retry knobs live on the map object itself.
            mode = mapspec.get("on_item_error", "fail")
            if mode not in ("fail", "skip", "collect"):
                raise ValueError("state %r map `on_item_error` must be one of fail/skip/collect (got %s)"
                                 % (name, _stringify(mode)))
            if mode != "fail" and ikind not in ("run", "search"):
                raise ValueError("state %r map `on_item_error` %r requires a run or search `do` (got %s)"
                                 % (name, mode, ikind))
            for k in ("retries", "backoff_ms"):
                v = mapspec.get(k, 0)
                if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                    raise ValueError("state %r map `%s` must be a non-negative integer" % (name, k))
            reducer = stepdef.get("reduce")
            if reducer is not None:
                if _kind_of(reducer) != "run":
                    raise ValueError("state %r map `reduce` must be a `run` step" % name)
                if reducer["run"] not in registry:
                    raise ValueError("state %r map `reduce` references unknown registry fn %r"
                                     % (name, reducer["run"]))
                _check_pure_fanout(name, "reduce", reducer)
        targets = [(v.get("to") if isinstance(v, dict) else v)
                   for v in (stepdef.get("routes") or {}).values()]
        targets += [c.get("to") for c in stepdef.get("when", [])]
        if "next" in stepdef:
            targets.append(stepdef["next"])
        for t in targets:
            if t not in legal:
                raise ValueError("state %r routes to unknown target %r" % (name, t))
    return states, legal


def _check_callers(states, llm, search, router=None):
    # wf: the spec statically reveals every kind used (map inners included) — fail at LOAD, not hours
    # in at runtime past human gates. The raises in _exec_kind stay as backstops (same messages).
    used = set()
    routed_model = False
    for stepdef in states.values():
        kind = _kind_of(stepdef)
        used.add(kind)
        if kind == "prompt" and stepdef.get("routes"):
            routed_model = True
        if kind == "map":
            used.add(_kind_of(stepdef["map"]["do"]))
    if routed_model and router is None and llm is None:
        raise ValueError("a routed prompt state needs a router caller "
                         "(load_workflow(..., router=) — defaults to llm when provided)")
    if "prompt" in used and llm is None:
        raise ValueError("a `prompt` state needs an llm caller (load_workflow(..., llm=))")
    if "search" in used and search is None:
        raise ValueError("a `search` state needs a search caller (load_workflow(..., search=))")


# ----------------------------------------------------------------------------- spec loaders (JSON always; YAML optional)
def read_spec(path):
    """Load a workflow spec from a file. `.json` uses the stdlib; `.yaml`/`.yml` uses a YAML parser
    IFF one is importable (`pip install pyyaml`), else a clear error. The interpreter is otherwise
    format-agnostic — the canonical/portable form is JSON; YAML is an authoring convenience."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml
        except ImportError:
            raise ValueError("cannot read %r: no YAML parser (pip install pyyaml, or use JSON)" % path)
        return yaml.safe_load(text)
    return json.loads(text)


def load_workflow_file(path, registry, **kw):
    """Convenience: read a spec file (JSON or YAML) and build the Flow. See load_workflow for kwargs."""
    return load_workflow(read_spec(path), registry, **kw)


# ----------------------------------------------------------------------------- the interpreter
def load_workflow(spec, registry, llm=None, search=None, router=None,
                  max_repair=MAX_REPAIR, max_intervene=MAX_INTERVENE):
    """Compile a data workflow into a Flow that rides the durable engine. `router` is the
    independent edge judge for routed prompt steps (defaults to the `llm` caller — bind a
    cheap classification model here in production)."""
    states, legal_targets = _validate_spec(spec, registry)
    _check_callers(states, llm, search, router)
    fid = spec.get("id", "workflow")
    version = spec.get("version", 1)
    order = list(states)                    # wf: declaration order IS the sequential fall-through order
    default_context = spec.get("context")
    if default_context is not None and default_context not in ("shared", "isolated"):
        raise ValueError("spec `context` must be \"shared\" or \"isolated\" (got %s)"
                         % _stringify(default_context))

    def fn(ctx, inp):
        state = {"input": inp}
        flowing = inp
        histories = {}                      # wf: per-group conversational memory (rebuilt on replay)
        visits = {}
        current = spec["start"]
        while current not in ("@done", "@fail"):
            stepdef = states[current]
            kind = _kind_of(stepdef)
            n = visits.get(current, 0)
            if n >= MAX_VISITS:
                # wf: the loop guard — bounds model↔model cycles with no human gate or data predicate.
                raise RuntimeError("state %r exceeded the visit cap of %d — likely an unbounded cycle"
                                   % (current, MAX_VISITS))
            visits[current] = n + 1
            skey = "%s#%d" % (current, n)   # wf: per-visit key (loop-safe)
            intent = stepdef.get("intent")

            ret = {}                        # LLM/human structured return (set/append/delete + next)
            goto = None                     # wf: out-of-band failure route (on_error `to`)
            rules = _on_error_rules(stepdef)
            if kind == "map":
                result = _do_map(ctx, skey, stepdef, state, flowing, legal_targets,
                                 registry, llm, search, intent, max_repair, max_intervene)
            else:
                on_fail = _compile_on_fail(rules) if rules else None
                result, ret, goto = _exec_kind(ctx, skey, kind, stepdef, state, flowing, histories,
                                               legal_targets, registry, llm, search, intent,
                                               max_repair, max_intervene, on_fail=on_fail,
                                               idempotent=stepdef.get("idempotent", True),
                                               router=router,
                                               default_context=default_context)

            state[current] = result         # wf: auto-store the result under $.<state>
            if rules and _is_error_sentinel(result):
                # wf: a CAUGHT failure — the journal holds the sentinel (forensic truth); re-match
                # the rules on EVERY pass to re-derive `to`/`result` (same spec + same sentinel =>
                # same branch, so the walk is replay-deterministic). A rule `result` is a pure
                # replay-time substitution with `@` bound to the sentinel.
                rule = _match_rule(rules, result["__error__"])
                if rule is not None:
                    if "result" in rule:
                        value = _resolve_deep(rule["result"], state, flowing, result)
                        state[current] = value
                        result = value
                    if "to" in rule:
                        goto = rule["to"]
            _apply_ops(state, flowing, result, stepdef)   # authored static mutations (@ = result, in = input)
            _apply_ops(state, flowing, result, ret)       # LLM/human-emitted mutations
            flowing = result

            current = goto if goto is not None else _route(
                stepdef, registry, state, result, ret.get("next"), order, current)

        if current == "@fail":
            raise RuntimeError("workflow %s reached @fail at the previous step" % fid)
        return {"result": flowing, "state": state}

    return Flow(fn, fid, version)


def _exec_kind(ctx, skey, kind, stepdef, state, flowing, histories, legal_targets,
               registry, llm, search, intent, max_repair, max_intervene, on_fail=None,
               idempotent=True, router=None, default_context=None):
    """Execute ONE non-map kind -> (result, ret, goto). The single source of truth for kind
    semantics, shared by the main loop and `map`'s per-item dispatch. `run`/`search` return
    `ret={}` (data, not routing); `prompt` runs the validate->repair->intervene loop;
    `ask` is a human-input gate. `goto` is the OUT-OF-BAND failure route (on_error `to`) —
    deliberately not smuggled through `ret`, so a model emitting a route key can never hijack
    routing past the legality checks. A `map` reaching here (as an inner `do`) raises — nested
    map is out of scope this pass. `on_fail` is the compiled failure policy for run/search."""
    if kind == "run":
        fnref = registry[stepdef["run"]]
        if idempotent:
            result = ctx.step(skey, lambda fnref=fnref, flowing=flowing, state=state:
                              fnref(flowing, dict(state)), desc=intent, on_fail=on_fail)
        else:
            # wf: non-idempotent — the run fn contract widens to (flowing, state, idem_key); the
            # engine injects idem_key (py: by param name; js: arg0) and we forward it as arg 3 so
            # the downstream system can dedupe a crash-window re-run. A dangling start escalates
            # in-doubt (exit 11) -> resume --resolve; on_error handles application THROWS, in-doubt
            # handles crash UNCERTAINTY — the two compose, they never overlap.
            result = ctx.step(skey, lambda idem_key, fnref=fnref, flowing=flowing, state=state:
                              fnref(flowing, dict(state), idem_key),
                              idempotent=False, desc=intent, on_fail=on_fail)
        return result, {}, None
    if kind == "prompt":
        if llm is None:
            raise ValueError("a `prompt` state needs an llm caller (load_workflow(..., llm=))")
        caller = (lambda convo, _llm=llm: _llm(convo))
        return _do_model_step(ctx, skey, stepdef, "prompt", state, flowing, histories,
                              legal_targets, caller, "llm", intent, max_repair, max_intervene,
                              router or llm, registry=registry, default_context=default_context)
    if kind == "search":
        if search is None:
            raise ValueError("a `search` state needs a search caller (load_workflow(..., search=))")
        # wf: render the query template, then memoize the injected web caller like a `run` step. The
        # structured result auto-stores at $.<step> -> routable as $.<step>.results[0].url.
        query = _render_template(stepdef["search"], state, flowing)
        fmt = stepdef.get("format", "structured")
        result = ctx.step(skey, lambda q=query, f=fmt: search(q, f), desc=intent, on_fail=on_fail,
                          in_hash=_in_hash([query, fmt]))
        return result, {}, None
    if kind == "ask":
        result, ret = _do_ask(ctx, skey, stepdef, state, flowing, intent, histories, default_context)
        return result, ret, None
    raise ValueError("kind %r cannot be executed here (nested `map` is not supported)" % kind)


def _do_map(ctx, skey, stepdef, state, flowing, legal_targets,
            registry, llm, search, intent, max_repair, max_intervene):
    """Sequential map-reduce. `over` (a `$`-path) must resolve to a list; each item runs the inner
    `do` stepdef (ANY single kind) under a durable per-item key `<skey>/map#<i>`, with the item bound
    as the flowing input AND exposed under `$.<as>` (+ `$.<as>_index`). Per-item results collect into
    a list; an optional `reduce` (a `run`) folds them and its output flows, else the list flows.

    Map is a PURE FAN-OUT: the inner step's routing (`next`) and emitted mutations (`set`/`append`)
    do NOT touch global state — only this step's own mutations + `reduce` write. Each item gets a
    FRESH conversational history, so per-item prompt calls never bleed across items."""
    mapspec = stepdef["map"]
    root, toks = _parse_path(mapspec["over"])
    items = _resolve(state, None, root, toks)
    if not isinstance(items, list):
        raise ValueError("map `over` %r did not resolve to a list (got %s)"
                         % (mapspec["over"], type(items).__name__))
    as_name = mapspec.get("as", "it")
    inner = mapspec["do"]
    inner_kind = _kind_of(inner)
    inner_intent = inner.get("intent", intent)
    # wf: per-item failure policy — `on_item_error` fail (default: one throw kills the flow) |
    # collect (the sentinel stays in the list at its position) | skip (memoized as journal truth,
    # omitted from outs — the omission is recomputed from memoized values each pass, deterministic).
    # `retries`/`backoff_ms` on the map apply per item in every mode.
    mode = mapspec.get("on_item_error", "fail")
    retries = mapspec.get("retries", 0)
    backoff_ms = mapspec.get("backoff_ms", 0)
    catch = mode != "fail"
    outs = []
    for i, item in enumerate(items):
        item_state = dict(state)
        item_state[as_name] = item
        item_state[as_name + "_index"] = i
        on_fail = _item_policy(retries, backoff_ms, catch) if (catch or retries) else None
        res, _ret, _goto = _exec_kind(ctx, "%s/map#%d" % (skey, i), inner_kind, inner, item_state, item,
                                      {}, legal_targets, registry, llm, search,
                                      inner_intent, max_repair, max_intervene, on_fail=on_fail)
        if catch and _is_error_sentinel(res) and mode == "skip":
            continue
        outs.append(res)
    if "reduce" in stepdef:
        reducer = stepdef["reduce"]
        if _kind_of(reducer) != "run":
            raise ValueError("map `reduce` must be a `run` step")
        fnref = registry[reducer["run"]]
        return ctx.step("%s/reduce" % skey, lambda fnref=fnref, outs=list(outs), state=state:
                        fnref(outs, dict(state)), desc=intent)
    return outs


def _route(stepdef, registry, state, result, next_label, order, current):
    # wf: routing — `when` predicates first (mechanical rails beat everything), then the emitted
    # label through `routes` (routes are binding: a mapped label routes; an unmapped one — legal only
    # on `"optional": true` steps / free-text asks — falls onward), then the declared `next`, then
    # SEQUENTIAL FALL-THROUGH: the next state in declaration order; the last declared state -> @done.
    for cond in stepdef.get("when", []):
        if _eval_pred(cond["if"], registry, state, result):
            return cond["to"]
    routes = stepdef.get("routes")
    if next_label is not None:
        target = _route_target(routes, next_label) if routes else next_label
        if target is not None:
            return target
    if "next" in stepdef:
        return stepdef["next"]
    i = order.index(current)
    return order[i + 1] if i + 1 < len(order) else "@done"


def _as_decision_request(ret):
    # wf: a model reply asking a human for input instead of resolving to a value — the ASK: line
    # convention: a reply whose first non-blank line (after fence strip) starts with `ASK:` (taught
    # by _ASK_RULE, same words in the same position on every call, so detection is a stable
    # contract). Drives enriched-context interruptibility (NOT a routing/validation error).
    if isinstance(ret, str):
        text = ret.strip()
        if text.startswith("```"):
            nl = text.find("\n")
            text = (text[nl + 1:] if nl != -1 else text[3:]).strip()
        first = text.split("\n", 1)[0].strip()
        if first.startswith("ASK:"):
            return {"prompt": first[4:].strip() or "The model needs more information."}
    return None


def _do_model_step(ctx, skey, stepdef, prompt_field, state, flowing, histories,
                   legal_targets, caller, tag, intent, max_repair, max_intervene, router,
                   registry=None, default_context=None):
    """One model step, v2: the TASK/ROUTER split.

    TASK — `caller(convo)` runs the author's directive (a pure user message under the engine's
    system prefix) and returns raw text: either an `ASK:` line (-> durable human gate; the answer is
    woven back and the task RE-ATTEMPTS) or ONE discrete JSON object (tolerantly extracted; not
    discernible -> a bounded repair prompt). Agent callers may return {"text", "set"} — `set` is the
    caller-captured live set_state channel (infra, not model-controlled), folded into the returned
    ops. A caller returning a plain dict is treated as the already-parsed value (stub convenience).

    ROUTING — only when the step declares `routes`: the system message LEADS with the outcome
    contract, and the reply's self-declared `"outcome"` is inspected MECHANICALLY (fast path, zero
    judge calls; `proceed` legal on `"optional": true` steps; missing/off-menu -> a bounded repair
    round). The independent, isolated JUDGE (`_run_router`) fires only as the FALLBACK, and owns
    `ask` — the CAN'T-ROUTE path: its stated reason is shown to the human, the answer is woven into
    the TASK convo, and the task re-attempts. Router repairs are bounded; exhaustion FORCES the ask.

    Budgets truly spent -> a clean flow failure. Nobody hand-picks edges: humans inform, the judge
    routes, `ask`-kind gates are the authored human-routing surface.
    """
    # wf: SHARED by default — the flow's model steps are one continuous conversation (steps are
    # directed prompts into a persistent context; human answers weave in as user turns). Isolation
    # is the OPT-OUT: step-level `"context": "isolated"` drops a step out of the thread; the
    # spec-level `context: "isolated"` makes the whole flow lean. Map fan-out stays per-item-fresh
    # regardless (fresh histories per item) and the router judge is always isolated.
    label = stepdef.get("context") or default_context or "shared"
    if label == "isolated":
        label = "__isolated__:%s" % skey
    history = histories.setdefault(label, [])
    rendered = _render_template(stepdef[prompt_field], state, flowing)
    routes = stepdef.get("routes")

    # wf: the EXPECTED OUTPUT leads the system message ("in front of the prompt") on routed steps —
    # the exit contract is declared before the directive, and the reply is inspected mechanically.
    if routes:
        proceed_line = _OUTCOME_PROCEED if stepdef.get("optional") else ""
        system = (_OUTCOME_CONTRACT % (_outcomes_text(routes) + "\n", proceed_line)) + _TASK_SYSTEM
    else:
        system = _TASK_SYSTEM
    convo = ([{"role": "system", "content": system}] + list(history)
             + [{"role": "user", "content": rendered}])

    r = route_r = repairs = interventions = 0
    raw = ""
    value = None

    def _gate(question, options=None):
        # wf: ONE durable intervene gate mechanism for both the worker's ASK and the router's ask.
        schema = {"enum": options} if options else None
        return ctx.ask("%s/intervene#%d" % (skey, interventions),
                       {"prompt": question, "options": options}, schema=schema, desc=intent)

    while True:
        # ---- TASK round: call, then walk the discernment ladder --------------------------------
        ret = ctx.step("%s/%s#%d" % (skey, tag, r),
                       lambda convo=list(convo): caller(convo), desc=intent,
                       in_hash=_in_hash(convo))
        r += 1
        live_set = None
        if isinstance(ret, dict) and "text" in ret:
            raw, live_set = ret["text"], ret.get("set")
        elif isinstance(ret, dict):
            raw, value = _stringify(ret), ret               # stub convenience: already parsed
        else:
            raw, value = (ret if isinstance(ret, str) else _stringify(ret)), None

        dreq = _as_decision_request(raw)
        if dreq is not None:
            if interventions >= max_intervene:
                value = None
                break
            ans = _gate(dreq.get("prompt", "The model needs more information."), dreq.get("options"))
            interventions += 1
            convo.append({"role": "assistant", "content": raw})
            convo.append({"role": "user", "content": _HUMAN_ANSWER % _stringify(ans)})
            continue                                        # task re-attempt

        if value is None:
            value = _extract_json_object(raw)
        if value is None:                                   # not discernible -> repair the task
            if repairs >= max_repair:
                break
            repairs += 1
            convo.append({"role": "assistant", "content": raw})
            convo.append({"role": "user", "content": _JSON_REPAIR})
            continue

        # ---- ROUTING (routes only; a matching `when` rail wins for FREE) ------------------------
        if not routes:
            history.append({"role": "user", "content": rendered})
            history.append({"role": "assistant", "content": raw})
            return value, {"set": live_set} if live_set else {}, None
        # wf: evaluate the mechanical rails against the POST-STORE view (state as _route will see
        # it) — a match means _route takes the rail regardless of any judgment, so inspection and
        # the router would be pure waste. skey is always `<state>#<visit>` (map inners can't route).
        rail_state = dict(state)
        rail_state[skey.rsplit("#", 1)[0]] = value
        if any(_eval_pred(cond["if"], registry or {}, rail_state, value)
               for cond in stepdef.get("when", [])):
            history.append({"role": "user", "content": rendered})
            history.append({"role": "assistant", "content": raw})
            return value, {"set": live_set} if live_set else {}, None
        # wf: FAST PATH — the step declared its own exit per the prefixed contract; inspect it
        # mechanically (zero judge calls). Missing/off-menu -> one repair lane (shared budget) ->
        # the independent JUDGE as the fallback (which still owns the reasoned can't-route ask).
        declared = value.get("outcome") if isinstance(value, dict) else None
        if isinstance(declared, str) and declared in routes:
            history.append({"role": "user", "content": rendered})
            history.append({"role": "assistant", "content": raw})
            ret_ops = {"set": live_set} if live_set else {}
            ret_ops["next"] = declared
            return value, ret_ops, None
        if declared == "proceed" and stepdef.get("optional"):
            history.append({"role": "user", "content": rendered})
            history.append({"role": "assistant", "content": raw})
            return value, {"set": live_set} if live_set else {}, None
        if repairs < max_repair:
            repairs += 1
            convo.append({"role": "assistant", "content": raw})
            convo.append({"role": "user", "content": _OUTCOME_REPAIR % ", ".join(routes)})
            value = None
            continue
        verdict = _run_router(ctx, skey, stepdef, rendered, raw, router, intent,
                              max_repair, route_r)
        route_r = verdict.pop("_rounds")
        if verdict["outcome"] == "ask":
            if interventions >= max_intervene:
                value = None
                break
            ans = _gate(verdict.get("question") or "The step could not be routed. What should be "
                                                   "used to complete it?")
            interventions += 1
            convo.append({"role": "assistant", "content": raw})
            convo.append({"role": "user", "content": _HUMAN_ANSWER % _stringify(ans)})
            value = None
            continue                                        # task re-attempt, then a fresh judgment
        history.append({"role": "user", "content": rendered})
        history.append({"role": "assistant", "content": raw})
        ret_ops = {"set": live_set} if live_set else {}
        if verdict["outcome"] != "proceed":
            ret_ops["next"] = verdict["outcome"]
        return value, ret_ops, None

    # ---- budgets truly spent ---------------------------------------------------------------------
    # wf: no escalate gate — humans inform (the ask loop above), they do not hand-pick edges.
    raise RuntimeError("step %s exhausted its budgets (%d task attempts, %d interventions) without "
                       "a usable, routable reply" % (skey, r, interventions))


def _run_router(ctx, skey, stepdef, rendered, raw, router, intent, max_repair, route_r):
    """The independent edge judge — the FALLBACK when the step's self-declared outcome could not be
    repaired into a valid one. One isolated call (+ bounded repairs), memoized at `<skey>/route#<n>`.
    Returns {"outcome": label|proceed|ask, "question"?, "_rounds": next_n}. Repairs exhausted -> a
    FORCED `ask` whose question carries the reason (_CANT_PROCEED)."""
    routes = stepdef["routes"]
    optional = stepdef.get("optional", False)
    allowed = set(routes) | {"ask"} | ({"proceed"} if optional else set())
    system = _ROUTER_SYSTEM % (_ROUTER_PROCEED if optional else "")
    convo = [{"role": "system", "content": system},
             {"role": "user", "content": _ROUTER_USER % (rendered, raw, _outcomes_text(routes))}]
    why = "no verdict"
    for _ in range(1 + max_repair):
        ret = ctx.step("%s/route#%d" % (skey, route_r),
                       lambda convo=list(convo): router(convo), desc=intent,
                       in_hash=_in_hash(convo))
        route_r += 1
        verdict = ret if isinstance(ret, dict) else _extract_json_object(ret if isinstance(ret, str) else "")
        if isinstance(verdict, dict) and verdict.get("outcome") in allowed:
            if verdict["outcome"] == "ask" and not verdict.get("question"):
                why = "an `ask` outcome needs a `question` (why it cannot be routed)"
            else:
                out = {"outcome": verdict["outcome"], "_rounds": route_r}
                if "question" in verdict:
                    out["question"] = verdict["question"]
                return out
        else:
            why = ("no JSON outcome object found" if not isinstance(verdict, dict)
                   else "`outcome` %r is not one of %s" % (verdict.get("outcome"), sorted(allowed)))
        convo.append({"role": "assistant", "content": _stringify(ret)})
        convo.append({"role": "user", "content": _ROUTER_REPAIR % why})
    return {"outcome": "ask", "question": _CANT_PROCEED % (why + "."), "_rounds": route_r}


def _do_ask(ctx, skey, stepdef, state, flowing, intent, histories, default_context):
    rendered = _render_template(stepdef["ask"], state, flowing)
    options = stepdef.get("options")
    schema = {"enum": options} if options else None
    answer = ctx.ask(skey, {"prompt": rendered, "options": options}, schema=schema, desc=intent)
    # wf: GATES ARE TURNS — on the shared/agentic thread, the gate's question joins the conversation
    # as an assistant turn and the human's answer as a user turn, so later model steps (and loop
    # revisits) SEE the verdict without state holes. Same dial as model steps: `context: "isolated"`
    # on the gate keeps it out; an unanswered gate suspends BEFORE this point, so nothing is
    # appended until the answer exists (replay re-appends deterministically, walk order).
    label = stepdef.get("context") or default_context or "shared"
    if label != "isolated":
        history = histories.setdefault(label, [])
        history.append({"role": "assistant", "content": rendered})
        history.append({"role": "user", "content": _stringify(answer)})
    result = {"decision": answer}
    return result, {"next": answer if stepdef.get("routes") else None, "result": result}


# ----------------------------------------------------------------------------- default llm caller (production)
def llm_json(prompt, system="Reply with a single JSON object and nothing else.", model=None, url=None):
    """Best-effort OpenAI-compatible chat call (point RESUMABLE_LLM_URL at c-thru or any proxy).

    Returns the parsed JSON object (a plain dict return is treated by the interpreter as the
    already-parsed step value). Wrap it to accept a convo for `load_workflow(..., llm=)` — or
    return raw text from your own caller and let the engine own extraction/repair. Every call is
    memoized; replay never re-invokes the model."""
    import os
    import urllib.request

    url = url or os.environ.get("RESUMABLE_LLM_URL", "http://localhost:11434/v1/chat/completions")
    model = model or os.environ.get("RESUMABLE_LLM_MODEL", "gpt-4o-mini")
    body = json.dumps({"model": model, "temperature": 0, "messages": [
        {"role": "system", "content": system}, {"role": "user", "content": prompt}]}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    api_key = os.environ.get("RESUMABLE_LLM_KEY")
    if api_key:
        req.add_header("Authorization", "Bearer %s" % api_key)
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return json.loads(data["choices"][0]["message"]["content"])


# ----------------------------------------------------------------------------- tolerant JSON extraction
def _extract_json_object(raw):
    """Tolerant JSON extraction (ported from hermes_cli/goals.py): strip ``` fences, then take the
    first balanced {...} object. Hermes has no native JSON mode, so the agent reply may carry prose."""
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        s = s[nl + 1:] if nl != -1 else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
        s = s.strip()
    try:
        return json.loads(s)
    except ValueError:
        pass
    start = s.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except ValueError:
                        break
        start = s.find("{", start + 1)
    return None

