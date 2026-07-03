#!/usr/bin/env python3
"""spec.py — pure parser + linter for the define-done artifact (dod.md).

Reads the DoD grammar (see SKILL.md / spec_envelope.grammar_block) into plain dicts and
enforces the honesty rules in code: unknown markers, receipt-less ✓/~, dangling [after:]
references and duplicate ids are ERRORS; method-smell leaves (imperative phrasing with no
check clause) and check-less leaves are WARNINGS — checks are optional by design (jim,
2026-07-01), unreceipted claims are not.

Stdlib only; no file writes, no env, no LLM.
"""

import re

_STATE_RE = re.compile(r"STATE:\s*([A-Za-z]+)")
_GROUP_RE = re.compile(r"^\s*-\s+(R\d+)\s+(.*)$")
_LEAF_RE = re.compile(r"^\s*-\s+(R\d+(?:\.\d+)+)\s+(.*)$")
_AFTER_RE = re.compile(r"\[after:\s*([^\]]*)\]")
_CHECK_RE = re.compile(r"check:\s*(cmd|judge)\s*—\s*(.*)$")
_MARKER_RE = re.compile(r"(?:^|\s)([○✓~])(?=\s|$)")
_AMEND_RE = re.compile(r"^-\s+(\S+)\s+(R\d+(?:\.\d+)*)\s+(added|waived|split)\s+—\s+(.*)$")

# Imperative openers that signal an activity, not a world-state (the world-state test).
_METHOD_VERBS = frozenset(
    "add build call configure create delete deploy execute fix implement install invoke "
    "launch migrate refactor run start stop update use write".split())


def parse_state(text):
    """Canonical STATE token, normalized by prefix. None if unrecognizable."""
    m = _STATE_RE.search(text or "")
    if not m:
        return None
    tok = m.group(1).strip().upper()
    if tok.startswith("DRAFT"):
        return "draft"
    if tok.startswith("AGREE"):
        return "agreed"
    if tok.startswith("SATISF"):
        return "satisfied"
    return None


def _line_value(text, label):
    m = re.search(rf"^{label}[^:]*:\s*(.*)$", text or "", re.MULTILINE)
    return m.group(1).strip() if m else None


def _parse_after(rest):
    m = _AFTER_RE.search(rest)
    if not m:
        return [], rest
    refs = [p.strip() for p in m.group(1).split(",")
            if p.strip() and p.strip() not in ("—", "-")]
    return refs, (rest[:m.start()] + rest[m.end():]).strip()


def _parse_leaf_rest(rest):
    """<text> [check: kind — text] <marker> [receipt] → (text, check|None, marker|None, receipt)."""
    marker, receipt, head = None, "", rest
    last = None
    for m in _MARKER_RE.finditer(rest):
        last = m
    if last:
        marker = last.group(1)
        receipt = rest[last.end():].strip()
        head = rest[:last.start()].strip()
    check = None
    cm = _CHECK_RE.search(head)
    if cm:
        check = {"kind": cm.group(1), "text": cm.group(2).strip()}
        head = head[:cm.start()].strip()
    return head, check, marker, receipt


def parse_dod(text):
    """dod.md text → {state, intent, hard, soft, groups, open, amendments}.

    groups: [{id, text, after: [R-ids], items: [{id, text, check: {kind, text}|None,
    marker: ○|✓|~|None, receipt}]}]. Amendment lines that don't match the structured
    shape are kept raw under {"raw": line}.
    """
    text = text or ""
    groups, amendments = [], []
    in_amendments = False
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("AMENDMENTS"):
            in_amendments = True
            continue
        if in_amendments:
            if not s.startswith("- "):
                if s:
                    in_amendments = False
                continue
            am = _AMEND_RE.match(s)
            amendments.append(
                {"cycle": am.group(1), "id": am.group(2), "action": am.group(3),
                 "reason": am.group(4).strip()} if am else {"raw": s})
            continue
        lm = _LEAF_RE.match(ln)
        if lm:
            body, check, marker, receipt = _parse_leaf_rest(lm.group(2).strip())
            item = {"id": lm.group(1), "text": body, "check": check,
                    "marker": marker, "receipt": receipt}
            if groups:
                groups[-1]["items"].append(item)
            else:  # orphan leaf: synthesize a group so nothing is dropped
                groups.append({"id": None, "text": "", "after": [], "items": [item]})
            continue
        gm = _GROUP_RE.match(ln)
        if gm:
            after, body = _parse_after(gm.group(2).strip())
            groups.append({"id": gm.group(1), "text": body, "after": after, "items": []})

    return {"state": parse_state(text), "intent": _line_value(text, "INTENT"),
            "hard": _line_value(text, "HARD"), "soft": _line_value(text, "SOFT"),
            "groups": groups, "open": _line_value(text, "OPEN"),
            "amendments": amendments}


def leaves(parsed):
    return [it for g in parsed["groups"] for it in g["items"]]


def ids(parsed):
    out = [g["id"] for g in parsed["groups"] if g["id"]]
    out += [it["id"] for it in leaves(parsed)]
    return out


def lint(parsed):
    """→ (errors, warnings): honesty violations vs style smells."""
    errors, warnings = [], []
    if not parsed["intent"]:
        errors.append("missing INTENT line")
    all_ids = ids(parsed)
    seen = set()
    for i in all_ids:
        if i in seen:
            errors.append(f"duplicate id {i}")
        seen.add(i)
    for g in parsed["groups"]:
        for ref in g["after"]:
            if ref not in seen:
                errors.append(f"dangling [after:] reference {ref} on {g['id'] or '(orphan)'}")
    for it in leaves(parsed):
        if it["marker"] is None:
            errors.append(f"leaf {it['id']} has no marker (○/✓/~)")
        elif it["marker"] == "✓" and not it["receipt"]:
            errors.append(f"✓ without receipt on {it['id']}")
        elif it["marker"] == "~" and not it["receipt"]:
            errors.append(f"~ without receipted reason on {it['id']}")
        first = (it["text"].split() or [""])[0].lower()
        if first in _METHOD_VERBS and not it["check"]:
            warnings.append(f"method-smell on {it['id']}: starts with '{first}' and has "
                            f"no check — phrase as the world-state it produces")
        elif not it["check"]:
            warnings.append(f"no check on {it['id']} (marking it ✓ will require a receipt)")
    return errors, warnings


def unmet(parsed):
    """Leaf ids still to satisfy (unmet or unmarked; waived excluded)."""
    return [it["id"] for it in leaves(parsed) if it["marker"] not in ("✓", "~")]


def satisfied(parsed):
    """True iff there are leaves and every leaf is ✓ or ~ WITH a receipt."""
    ls = leaves(parsed)
    return bool(ls) and all(it["marker"] in ("✓", "~") and it["receipt"] for it in ls)
