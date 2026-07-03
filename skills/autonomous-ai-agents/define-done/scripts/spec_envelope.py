#!/usr/bin/env python3
"""spec_envelope.py — the canonical define-done invocation contract (single source).

The prompt framing that makes a bare `hermes -z` turn engage the define-done skill and
write dod.md where parsers expect it. Consumers import this module rather than keeping a
runtime copy; tests/test_contracts.py pins that every grammar token scripts/spec.py
parses is named in this instruction, so the writer instruction and the reader cannot
drift apart.

Stdlib-only; no env reads, no imports.
"""

SKILL_HEAD = (
    "Use the define-done skill: skill_view to load it, then follow it INCLUDING the "
    "world-state test (every requirement is a condition that IS TRUE at the end, never "
    "an activity). Produce the requirements artifact ONLY — do not plan methods, do not "
    "execute anything."
)


def grammar_block(specs_dir, slug):
    """The artifact-contract paragraph. Every token spec.py matches on is named here."""
    return (
        f"Write the definition of done to {specs_dir}/{slug}/dod.md exactly in the DoD "
        "grammar: an H1 '# DoD: <slug>   STATE: draft | agreed | satisfied' header; an "
        "'INTENT: <one sentence>' line (immutable — never edit it later); 'HARD "
        "(inviolable):' and 'SOFT (relaxable, ranked):' lines; a 'REQUIREMENTS' section "
        "of group lines '- R<N> <outcome> [after: <R-ids or —>]' each with indented "
        "leaf lines '- R<N>.<M> <requirement> [check: cmd — <command> | check: judge — "
        "<criterion>] <marker>' using markers ○ (unmet), ✓ (met — MUST be followed by a "
        "receipt), ~ (waived — MUST be followed by the reason); an 'OPEN:' line for "
        "unresolved ambiguities; and an 'AMENDMENTS:' section (empty on a fresh spec; "
        "changes append one '- <cycle> <R-id> <added|waived|split> — <reason>' line "
        "each). Fresh specs are STATE: draft with every leaf ○."
    )


def spec_prompt(intent, slug, specs_dir, extra=""):
    """Standard specifier prompt writing dod.md into the slug's spec dir."""
    mid = f"{extra}\n" if extra else ""
    return f"{SKILL_HEAD}\nINTENT: {intent}\n{mid}{grammar_block(specs_dir, slug)}"
