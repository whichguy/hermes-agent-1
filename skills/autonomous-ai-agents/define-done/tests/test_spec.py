#!/usr/bin/env python3
"""Unit tests for spec.py — the dod.md parser + linter. Pure, no LLM, no container.

Run: python3 tests/test_spec.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))

import spec  # noqa: E402

FIX = os.path.join(_HERE, "fixtures")


def read_fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


CLEAN = read_fixture("clean.dod.md")
SMELLY = read_fixture("smelly.dod.md")
SATISFIED = read_fixture("satisfied.dod.md")


class Parsing(unittest.TestCase):
    def test_header_and_frame(self):
        p = spec.parse_dod(CLEAN)
        self.assertEqual(p["state"], "agreed")
        self.assertIn("new schema", p["intent"])
        self.assertIn("no data loss", p["hard"])
        self.assertIn("zero downtime", p["soft"])
        self.assertIn("analytics replica", p["open"])

    def test_states_normalize_by_prefix(self):
        for tok, want in (("draft", "draft"), ("AGREED", "agreed"),
                          ("satisfied", "satisfied"), ("bogus", None)):
            self.assertEqual(spec.parse_state(f"# DoD: x   STATE: {tok}"), want)

    def test_groups_items_and_after(self):
        p = spec.parse_dod(CLEAN)
        self.assertEqual([g["id"] for g in p["groups"]], ["R1", "R2"])
        self.assertEqual(p["groups"][0]["after"], [])
        self.assertEqual(p["groups"][1]["after"], ["R1"])
        self.assertEqual([it["id"] for it in spec.leaves(p)],
                         ["R1.1", "R1.2", "R2.1", "R2.2"])

    def test_checks_markers_receipts(self):
        p = spec.parse_dod(CLEAN)
        by_id = {it["id"]: it for it in spec.leaves(p)}
        self.assertEqual(by_id["R1.1"]["check"]["kind"], "cmd")
        self.assertIn("tenant_id is null", by_id["R1.1"]["check"]["text"])
        self.assertEqual(by_id["R2.2"]["check"]["kind"], "judge")
        self.assertTrue(all(it["marker"] == "○" for it in spec.leaves(p)))
        done = {it["id"]: it for it in spec.leaves(spec.parse_dod(SATISFIED))}
        self.assertEqual(done["R1.1"]["marker"], "✓")
        self.assertIn("replay suite", done["R1.1"]["receipt"])
        self.assertEqual(done["R2.2"]["marker"], "~")
        self.assertIn("out of scope", done["R2.2"]["receipt"])
        self.assertIsNone(done["R2.2"]["check"])

    def test_amendments(self):
        p = spec.parse_dod(SATISFIED)
        self.assertEqual(len(p["amendments"]), 2)
        self.assertEqual(p["amendments"][0]["action"], "waived")
        self.assertEqual(p["amendments"][0]["id"], "R2.2")
        self.assertEqual(p["amendments"][1]["action"], "added")


class Lint(unittest.TestCase):
    def test_clean_is_silent(self):
        errors, warnings = spec.lint(spec.parse_dod(CLEAN))
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_each_error_rule_fires_on_smelly(self):
        errors, warnings = spec.lint(spec.parse_dod(SMELLY))
        joined = "\n".join(errors)
        self.assertIn("✓ without receipt on R1.2", joined)
        self.assertIn("~ without receipted reason on R2.2", joined)
        self.assertIn("dangling [after:] reference R9", joined)
        self.assertIn("duplicate id R2", joined)
        self.assertIn("leaf R2.1 has no marker", joined)
        wjoined = "\n".join(warnings)
        self.assertIn("method-smell on R1.1", wjoined)

    def test_missing_intent(self):
        errors, _ = spec.lint(spec.parse_dod("# DoD: x   STATE: draft\n"))
        self.assertIn("missing INTENT line", errors)

    def test_checkless_leaf_warns_but_is_legal(self):
        text = ("# DoD: x   STATE: draft\nINTENT: y\n"
                "- R1  group  [after: —]\n  - R1.1  the output file exists   ○\n")
        errors, warnings = spec.lint(spec.parse_dod(text))
        self.assertEqual(errors, [])
        self.assertTrue(any("no check on R1.1" in w for w in warnings))


class Satisfaction(unittest.TestCase):
    def test_satisfied_only_when_fully_receipted(self):
        self.assertTrue(spec.satisfied(spec.parse_dod(SATISFIED)))
        self.assertFalse(spec.satisfied(spec.parse_dod(CLEAN)))
        self.assertFalse(spec.satisfied(spec.parse_dod(SMELLY)))
        self.assertFalse(spec.satisfied(spec.parse_dod("# DoD: x STATE: draft\nINTENT: y\n")))

    def test_unmet_excludes_waived_and_met(self):
        self.assertEqual(spec.unmet(spec.parse_dod(SATISFIED)), [])
        self.assertEqual(spec.unmet(spec.parse_dod(CLEAN)),
                         ["R1.1", "R1.2", "R2.1", "R2.2"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
