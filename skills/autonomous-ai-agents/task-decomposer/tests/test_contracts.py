#!/usr/bin/env python3
"""Cross-skill contract tests — pin the drift surfaces between task-decomposer and its
neighbors WITHOUT runtime coupling. Each cross-skill class skips (not fails) when the
counterpart is not on disk, so this suite stays runnable standalone.

Surfaces pinned:
  - DefineDoneContract: the requirements seam — spec.py's R-id grammar is what
    coverage_violations/serves reference, and parse_dod's output keys are what
    report.completion_report reads (the dod travels as a parsed dict, never an import).
  - HarvestContract: relentless-solve's harvest.py is a DELIBERATE COPY of the fp +
    record fold that canonically lives here (planfile.fp / report.records_from_results)
    — pin them behaviorally so the copies cannot drift.
  - DodBlockLockstep: envelope._dod_block's instruction tokens name exactly the schema
    field and dispositions the validators enforce.

Run: python3 tests/test_contracts.py
"""

import importlib.util
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))

import envelope  # noqa: E402
import planfile  # noqa: E402
import report  # noqa: E402

_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))


def _resolve(env_var, skill):
    sibling = os.path.abspath(os.path.join(_HERE, "..", "..", skill, "scripts"))
    return (os.environ.get(env_var) or (sibling if os.path.isdir(sibling) else None)
            or os.path.join(_HOME, "skills", skill, "scripts"))


def _load(path, alias):
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_DD_SCRIPTS = _resolve("DEFINE_DONE_DIR", "define-done")
_SPEC = _load(os.path.join(_DD_SCRIPTS, "spec.py"), "dd_spec")
_DD_FIXTURE = os.path.join(_DD_SCRIPTS, "..", "tests", "fixtures", "clean.dod.md")

_RS_SCRIPTS = _resolve("RELENTLESS_SOLVE_DIR", "relentless-solve")
_HARVEST = _load(os.path.join(_RS_SCRIPTS, "harvest.py"), "rs_harvest")


def _plan_and_results():
    tasks = [{"id": "t1", "method": "use the REST api", "description": "d",
              "success_criterion": "c1", "intent_link": "l", "depends_on": [],
              "status": "pending"},
             {"id": "t2", "method": "patch the config", "description": "d",
              "success_criterion": "c2", "intent_link": "l", "depends_on": [],
              "status": "pending"},
             {"id": "t3", "method": "verify end to end", "description": "d",
              "success_criterion": "c3", "intent_link": "l", "depends_on": ["t2"],
              "status": "pending"}]
    plan = {"schema": planfile.SCHEMA_VERSION, "slug": "s", "cycle": 1,
            "disposition": "tasks", "rationale": "r", "question": None, "tasks": tasks}
    results = [{"id": "t1", "method": "use the REST api", "verdict": "failed",
                "evidence": "403 forbidden", "learnings": ["token lacks scope X"]},
               {"id": "t2", "method": "patch the config", "verdict": "worked",
                "evidence": "reread shows retries: 3"},
               {"id": "t3", "method": "verify end to end", "verdict": "skipped",
                "evidence": ""}]
    return plan, results


@unittest.skipUnless(_SPEC, f"define-done spec.py not found in {_DD_SCRIPTS!r}")
class DefineDoneContract(unittest.TestCase):
    def setUp(self):
        with open(_DD_FIXTURE, encoding="utf-8") as fh:
            self.parsed = _SPEC.parse_dod(fh.read())

    def test_unmet_ids_satisfy_the_serves_grammar(self):
        unmet = _SPEC.unmet(self.parsed)
        self.assertTrue(unmet, "clean fixture must carry unmet leaves")
        tasks = [{"id": f"t{i}", "method": f"m{i}", "description": "d",
                  "success_criterion": "c", "intent_link": "l", "depends_on": [],
                  "status": "pending", "serves": [rid]}
                 for i, rid in enumerate(unmet)]
        plan = {"schema": planfile.SCHEMA_VERSION, "slug": "s", "cycle": 0,
                "disposition": "tasks", "rationale": "r", "question": None,
                "tasks": tasks}
        self.assertEqual(planfile.validate(plan), [])
        self.assertEqual(
            planfile.coverage_violations(plan, unmet, known_ids=set(_SPEC.ids(self.parsed))),
            [])

    def test_parse_dod_output_keys_completion_report_reads(self):
        self.assertIn("groups", self.parsed)
        for g in self.parsed["groups"]:
            self.assertIn("id", g)
            for it in g["items"]:
                self.assertIn("id", it)
                self.assertIn("marker", it)
                self.assertIn(it["marker"], ("○", "✓", "~", None),
                              "marker vocabulary is what the rollup switches on")

    def test_completion_report_rolls_up_a_real_dod(self):
        unmet = _SPEC.unmet(self.parsed)
        plan = {"schema": planfile.SCHEMA_VERSION, "slug": "s", "cycle": 0,
                "disposition": "tasks", "rationale": "r", "question": None,
                "tasks": [{"id": "t1", "method": "m", "description": "d",
                           "success_criterion": "c", "intent_link": "l",
                           "depends_on": [], "status": "pending", "serves": unmet}]}
        rep = report.completion_report(plan, [{"id": "t1", "method": "m",
                                               "verdict": "worked", "evidence": "ok"}],
                                       dod_parsed=self.parsed)
        self.assertEqual(set(rep["requirements"]),
                         {it["id"] for g in self.parsed["groups"] for it in g["items"]})
        self.assertTrue(all(v == "met" for v in rep["requirements"].values()))
        self.assertEqual(rep["status"], "complete")


@unittest.skipUnless(_HARVEST, f"relentless-solve harvest.py not found in {_RS_SCRIPTS!r}")
class HarvestContract(unittest.TestCase):
    """harvest.py (relentless-solve) is a deliberate copy of the canonical fold here —
    behavioral lockstep or the ledger and the completion report tell different stories
    about the same cycle."""

    def test_fp_parity(self):
        for s in ("Use the REST api!", "use   the rest API", "", None,
                  "ok migrate-with-pgloader", "Tried X: failed — timeout"):
            self.assertEqual(planfile.fp(s), _HARVEST.fp(s))

    def test_record_fold_parity_modulo_source(self):
        plan, results = _plan_and_results()
        ours = report.records_from_results(plan, results, cycle=1)
        theirs = _HARVEST.harvest_tasks(plan, results, cycle=1)
        self.assertEqual(len(ours), len(theirs))
        for a, b in zip(ours, theirs):
            self.assertEqual(a["source"], "report")
            self.assertEqual(b["source"], "harvest")
            for key in ("cycle", "kind", "text", "fp", "meta"):
                self.assertEqual(a[key], b[key],
                                 f"fold drift on {key!r}: {a} vs {b}")


class DodBlockLockstep(unittest.TestCase):
    def test_block_names_the_schema_field_and_the_honest_outs(self):
        block = envelope._dod_block(["R1.1", "R2.2"])
        self.assertIn('"serves"', block)
        self.assertIn("R1.1, R2.2", block)
        for disposition in ("exhausted", "needs_decision"):
            self.assertIn(disposition, block,
                          "the block must name the dispositions coverage checking "
                          "treats as honest outs")

    def test_prompts_and_validators_agree_on_absence(self):
        self.assertEqual(envelope._dod_block(None), "")
        self.assertEqual(envelope._dod_block([]), "")
        plan, _ = _plan_and_results()
        self.assertEqual(planfile.coverage_violations(plan, []), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
