#!/usr/bin/env python3
"""Cross-skill contract tests — pin the drift surfaces between relentless-solve and
resilient-planner WITHOUT runtime coupling. Each class skips (not fails) when the
counterpart skill is not on disk, so this suite stays runnable standalone.

Surfaces pinned:
  - EnvelopeContract: relentless's inline planner_envelope() == the planner's canonical
    scripts/envelope.py real_prompt() (the planner owns its invocation contract).
  - GrammarContract: harvest.py and drive.py parse the same plan-tree fixtures to the
    same STATE and the same dead/done node-id sets (the regexes are duplicated by design;
    this is what keeps them byte-equivalent in behavior).

Run: python3 tests/test_contracts.py
"""

import importlib.util
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))

import harvest  # noqa: E402
import relentless  # noqa: E402

_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
_PLANNER_SCRIPTS = (os.path.dirname(os.environ["RESILIENT_DRIVE"])
                    if os.environ.get("RESILIENT_DRIVE")
                    else os.path.join(_HOME, "skills", "resilient-planner", "scripts"))
FIX = os.path.join(_HERE, "fixtures")


def _load(name):
    path = os.path.join(_PLANNER_SCRIPTS, f"{name}.py")
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location(f"planner_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ENVELOPE = _load("envelope")
_DRIVE = _load("drive")


def read_fixture(name):
    with open(os.path.join(FIX, name, "plan-tree.md"), encoding="utf-8") as fh:
        return fh.read()


@unittest.skipUnless(_ENVELOPE, f"planner envelope.py not found in {_PLANNER_SCRIPTS!r}")
class EnvelopeContract(unittest.TestCase):
    def test_planner_envelope_matches_canonical(self):
        body = 'Do the thing.\n\n## Established facts (do not re-derive)\n- F1'
        ours = relentless.planner_envelope(body, "slug-c3")
        theirs = _ENVELOPE.real_prompt(body, "slug-c3", relentless.PLANS_DIR)
        self.assertEqual(ours, theirs)

    def test_marker_map_block_matches(self):
        self.assertIn(_ENVELOPE.marker_map_block(relentless.PLANS_DIR, "s-c0"),
                      relentless.planner_envelope("x", "s-c0"))


@unittest.skipUnless(_DRIVE, f"planner drive.py not found in {_PLANNER_SCRIPTS!r}")
class GrammarContract(unittest.TestCase):
    FIXTURES = ("exhaustion", "guard-halt", "success")

    def test_state_parsing_agrees(self):
        for name in self.FIXTURES:
            text = read_fixture(name)
            self.assertEqual(_DRIVE.parse_state(text), harvest.parse_state(text),
                             f"STATE disagreement on fixture {name}")
        self.assertEqual(_DRIVE.parse_state("# Plan-Tree: x   STATE: active"),
                         harvest.parse_state("# Plan-Tree: x   STATE: active"))

    def test_node_sets_agree(self):
        for name in self.FIXTURES:
            text = read_fixture(name)
            fp = _DRIVE.fingerprint(text)  # (state, dead ids, done ids, frontier)
            parsed = harvest.parse_plan_tree(text)
            self.assertEqual(sorted(d["id"] for d in parsed["dead"]), sorted(fp[1]),
                             f"dead-set disagreement on fixture {name}")
            self.assertEqual(sorted(d["id"] for d in parsed["done"]), sorted(fp[2]),
                             f"done-set disagreement on fixture {name}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
