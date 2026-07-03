#!/usr/bin/env python3
"""Contract tests — pin the drift surface between the writer instruction
(spec_envelope.py) and the reader (spec.py): every grammar token the parser matches must
be named in the instruction the model actually receives, or the two drift apart silently
(the GUARD-HALT lesson from relentless-solve).

Run: python3 tests/test_contracts.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))

import spec_envelope  # noqa: E402

PROMPT = spec_envelope.spec_prompt("toy intent", "toy-slug", "/opt/data/specs")

# Every token spec.py's regexes match on. A parser change that adds a token must add it
# here AND to grammar_block, keeping instruction and reader in lockstep.
GRAMMAR_TOKENS = ("# DoD:", "STATE:", "draft", "agreed", "satisfied", "INTENT:",
                  "HARD", "SOFT", "REQUIREMENTS", "○", "✓", "~", "[after:", "check:",
                  "cmd", "judge", "—", "OPEN:", "AMENDMENTS", "receipt")


class InstructionParserContract(unittest.TestCase):
    def test_every_grammar_token_is_instructed(self):
        for tok in GRAMMAR_TOKENS:
            self.assertIn(tok, PROMPT, f"grammar token {tok!r} missing from spec_prompt "
                                       f"— spec.py parses it but the model is never told")

    def test_artifact_path_pinned_to_slug(self):
        self.assertIn("/opt/data/specs/toy-slug/dod.md", PROMPT)

    def test_intent_verbatim_and_immutability_stated(self):
        self.assertIn("INTENT: toy intent", PROMPT)
        self.assertIn("immutable", PROMPT)

    def test_receipt_rules_stated(self):
        # the honesty rule the linter enforces must be in the instruction too
        self.assertIn("MUST be followed by a receipt", PROMPT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
