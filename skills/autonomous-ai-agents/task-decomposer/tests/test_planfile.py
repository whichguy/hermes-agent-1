#!/usr/bin/env python3
"""Unit tests for planfile.py — pure validation, no container, no network. Run:
    python3 tests/test_planfile.py
"""

import copy
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))

import envelope  # noqa: E402
import planfile  # noqa: E402

GOLDEN_PATH = os.path.join(_HERE, "fixtures", "plan-golden.json")


def golden():
    with open(GOLDEN_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def tasks_plan(*tasks, **over):
    base = {"schema": planfile.SCHEMA_VERSION, "slug": "s", "cycle": 0,
            "disposition": "tasks", "rationale": "r", "question": None,
            "tasks": list(tasks)}
    base.update(over)
    return base


def task(tid, dep=None, **over):
    base = {"id": tid, "method": f"method {tid}", "description": f"do {tid}",
            "success_criterion": f"{tid} observably done",
            "intent_link": f"{tid} is the best next step toward the intent",
            "depends_on": dep or [], "status": "pending"}
    base.update(over)
    return base


class GoldenFixture(unittest.TestCase):
    def test_golden_validates_clean(self):
        self.assertEqual(planfile.validate(golden()), [])

    def test_golden_dependencies_are_a_forward_dag(self):
        ids = [t["id"] for t in golden()["tasks"]]
        for t in golden()["tasks"]:
            for d in t["depends_on"]:
                self.assertLess(ids.index(d), ids.index(t["id"]))


class Dispositions(unittest.TestCase):
    def test_tasks_requires_at_least_one(self):
        v = planfile.validate(tasks_plan())
        self.assertTrue(any("at least one task" in s for s in v))

    def test_tasks_capped(self):
        many = tasks_plan(*[task(f"t{i}") for i in range(planfile.MAX_TASKS + 1)])
        self.assertTrue(any("at most" in s for s in planfile.validate(many)))
        exactly = tasks_plan(*[task(f"t{i}") for i in range(planfile.MAX_TASKS)])
        self.assertEqual(planfile.validate(exactly), [])

    def test_needs_decision_requires_question_and_no_tasks(self):
        ok = tasks_plan(disposition="needs_decision", question="Which region?", tasks=[])
        self.assertEqual(planfile.validate(ok), [])
        v = planfile.validate(tasks_plan(disposition="needs_decision", tasks=[]))
        self.assertTrue(any("question" in s for s in v))
        v = planfile.validate(tasks_plan(task("t1"), disposition="needs_decision",
                                         question="Which?"))
        self.assertTrue(any("no tasks" in s for s in v))

    def test_exhausted_carries_no_tasks(self):
        self.assertEqual(planfile.validate(tasks_plan(disposition="exhausted", tasks=[])), [])
        v = planfile.validate(tasks_plan(task("t1"), disposition="exhausted"))
        self.assertTrue(any("no tasks" in s for s in v))

    def test_unknown_disposition_and_schema(self):
        v = planfile.validate(tasks_plan(task("t1"), disposition="maybe", schema=99))
        self.assertTrue(any('"schema"' in s for s in v))
        self.assertTrue(any('"disposition"' in s for s in v))

    def test_stale_schema_version_rejected(self):
        # schema=1 plans (pre-intent_link) must not silently validate under schema 2.
        v = planfile.validate(tasks_plan(task("t1"), schema=1))
        self.assertTrue(any('"schema"' in s for s in v))

    def test_non_object_plan(self):
        self.assertEqual(planfile.validate([1, 2]), ["plan must be a JSON object"])


class TaskRules(unittest.TestCase):
    def test_id_regex_is_the_step_key_firewall(self):
        for bad in ("T1", "a b", "-lead", "x" * 17, ""):
            v = planfile.validate(tasks_plan(task(bad)))
            self.assertTrue(any(".id" in s for s in v), f"id {bad!r} must be rejected")
        self.assertEqual(planfile.validate(tasks_plan(task("a-1"), task("0z"))), [])

    def test_duplicate_ids_rejected(self):
        v = planfile.validate(tasks_plan(task("t1"), task("t1")))
        self.assertTrue(any("duplicates" in s for s in v))

    def test_depends_on_only_earlier_ids(self):
        v = planfile.validate(tasks_plan(task("t1", dep=["t2"]), task("t2")))
        self.assertTrue(any("EARLIER" in s for s in v))
        v = planfile.validate(tasks_plan(task("t1", dep=["t1"])))
        self.assertTrue(any("EARLIER" in s for s in v), "self-dependency must be rejected")
        self.assertEqual(planfile.validate(tasks_plan(task("t1"), task("t2", dep=["t1"]))), [])

    def test_required_strings(self):
        v = planfile.validate(tasks_plan(task("t1", method="  ")))
        self.assertTrue(any(".method" in s for s in v))
        v = planfile.validate(tasks_plan(task("t1", success_criterion=None)))
        self.assertTrue(any(".success_criterion" in s for s in v))

    def test_intent_link_required(self):
        v = planfile.validate(tasks_plan(task("t1", intent_link="")))
        self.assertTrue(any(".intent_link" in s for s in v))
        v = planfile.validate(tasks_plan(task("t1", intent_link=None)))
        self.assertTrue(any(".intent_link" in s for s in v))

    def test_forbidden_ids_rejects_collision(self):
        # a no-op for ordinary whole-cycle plans (no forbidden_ids passed)
        self.assertEqual(planfile.validate(tasks_plan(task("t1"))), [])
        v = planfile.validate(tasks_plan(task("t1")), forbidden_ids={"t1"})
        self.assertTrue(any("collides" in s for s in v))
        # an id NOT in forbidden_ids is unaffected
        self.assertEqual(planfile.validate(tasks_plan(task("t2")), forbidden_ids={"t1"}), [])

    def test_emitted_plans_must_be_pending(self):
        v = planfile.validate(tasks_plan(task("t1", status="worked")))
        self.assertTrue(any("pending" in s for s in v))
        # ...but a driver receipt with verdicts written back is valid
        receipt = tasks_plan(task("t1", status="worked"), task("t2", status="skipped"))
        self.assertEqual(planfile.validate(receipt, emitted=False), [])
        v = planfile.validate(tasks_plan(task("t1", status="exploded")), emitted=False)
        self.assertTrue(any("status" in s for s in v))


class LoadDump(unittest.TestCase):
    def test_round_trip_and_paths(self):
        with tempfile.TemporaryDirectory() as d:
            path = planfile.plan_path(d)
            self.assertTrue(path.endswith("plan.json"))
            self.assertTrue(planfile.result_path(d, "t1").endswith("result-t1.json"))
            planfile.dump(golden(), path)
            self.assertEqual(planfile.load(path), golden())

    def test_load_tolerates_absent_and_garbage(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(planfile.load(os.path.join(d, "nope.json")))
            p = os.path.join(d, "bad.json")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            self.assertIsNone(planfile.load(p))
            with open(p, "w", encoding="utf-8") as fh:
                fh.write('["a", "list"]')
            self.assertIsNone(planfile.load(p))


class Envelope(unittest.TestCase):
    def test_plan_prompt_carries_body_path_and_rules(self):
        p = envelope.plan_prompt("INTENT\n\n## Dead ends — do NOT re-attempt these methods\n"
                                 "- Tried alfa", "/x/c2/plan.json")
        self.assertIn("TASK PLANNER", p)
        self.assertIn("INTENT", p)
        self.assertIn("Tried alfa", p)
        self.assertIn("/x/c2/plan.json", p)
        for rule in ("single agent turn", "VERIFICATION task", "needs_decision",
                     "exhausted", '"pending"'):
            self.assertIn(rule, p)

    def test_schema_example_validates(self):
        # The example we show the model must itself pass validation once filled in —
        # pin the shape by parsing it with the placeholders substituted.
        raw = envelope.SCHEMA_EXAMPLE.replace("<slug>", "s")
        obj = json.loads(raw)
        obj["cycle"] = 0
        clean = copy.deepcopy(obj)
        clean["tasks"][0].update(id="t1", method="m", description="d",
                                 success_criterion="c")
        self.assertEqual([v for v in planfile.validate(clean) if ".id" not in v], [])

    def test_retry_suffix_lists_violations(self):
        s = envelope.retry_suffix(["bad id", "too many tasks"])
        self.assertIn("FAILED validation", s)
        self.assertIn("- bad id", s)
        self.assertIn("- too many tasks", s)

    def test_strict_success_criterion_rule_and_example(self):
        p = envelope.plan_prompt("INTENT", "/x/plan.json")
        self.assertIn("OBJECTIVELY-CHECKABLE", p)
        self.assertIn("works correctly", p)  # the bad example
        self.assertIn("pytest", p)  # the good example

    def test_intent_link_rule_present(self):
        p = envelope.plan_prompt("INTENT", "/x/plan.json")
        self.assertIn("intent_link", p)
        self.assertIn("not to implementation mechanics", p)

    def test_decomposition_suggestion_is_illustrative_not_mandatory(self):
        p = envelope.plan_prompt("INTENT", "/x/plan.json")
        self.assertIn("SUGGESTION, not a mandate", p)
        self.assertIn("PREPARE -> BUILD -> DATA MIGRATE (if any) -> DEPLOY -> "
                     "SEAMLESS SWITCHOVER", p)
        self.assertIn("don't have this shape at all", p)
        # scoped to a fresh whole-cycle plan, NOT the mid-cycle tail-only replan
        replan = envelope.partial_replan_prompt("INTENT", "/x/replan-1.json", {"t1"})
        self.assertNotIn("SEAMLESS SWITCHOVER", replan)

    def test_partial_replan_prompt_shares_task_rules_and_names_forbidden_ids(self):
        p = envelope.partial_replan_prompt("BODY", "/x/c2/plan.json", {"t1", "t2"})
        self.assertIn("PARTIAL REPLAN", p)
        self.assertIn("BODY", p)
        self.assertIn("/x/c2/plan.json", p)
        self.assertIn("t1", p)
        self.assertIn("t2", p)
        # the shared _TASK_RULES block, so the two prompts can't drift on field rules
        self.assertIn("OBJECTIVELY-CHECKABLE", p)
        self.assertIn("intent_link", p)


class Alternatives(unittest.TestCase):
    """The OPTIONAL decision-record field: requested by BOTH prompts (shared block),
    NEVER binding — validate() must ignore it entirely, well-formed or garbage. The
    consumer-side tolerant fold is pinned by relentless-solve's test_journey.py."""

    def test_both_prompts_request_alternatives(self):
        for p in (envelope.plan_prompt("INTENT", "/x/plan.json"),
                  envelope.partial_replan_prompt("BODY", "/x/replan-1.json", {"t1"})):
            self.assertIn('"alternatives"', p)
            self.assertIn("why_not_now", p)
            self.assertIn("OPTIONAL", p)
            self.assertIn("not brainstorming", p)

    def test_schema_example_carries_the_field(self):
        self.assertIn('"alternatives"', envelope.SCHEMA_EXAMPLE)

    def test_validate_ignores_well_formed_alternatives(self):
        p = tasks_plan(task("t1"),
                       alternatives=[{"method": "other way", "why_not_now": "slower"}])
        self.assertEqual(planfile.validate(p), [])

    def test_validate_ignores_garbage_alternatives(self):
        # advisory capture must NEVER reject a plan — even a malformed field passes
        for garbage in ("not a list", [{"no": "method"}], [42], {"method": "x"}):
            self.assertEqual(planfile.validate(tasks_plan(task("t1"),
                                                          alternatives=garbage)), [],
                             f"alternatives={garbage!r} must not reject the plan")


class Fingerprint(unittest.TestCase):
    def test_fp_is_case_whitespace_punctuation_insensitive(self):
        self.assertEqual(planfile.fp("Try   the REST api!"), planfile.fp("try the rest-api"))
        self.assertNotEqual(planfile.fp("method a"), planfile.fp("method b"))

    def test_fp_shape_and_empty(self):
        self.assertRegex(planfile.fp("x"), r"^[0-9a-f]{16}$")
        self.assertEqual(planfile.fp(None), planfile.fp(""))


class Serves(unittest.TestCase):
    def test_serves_is_optional(self):
        self.assertEqual(planfile.validate(tasks_plan(task("t1"))), [])

    def test_serves_shape_checked_when_present(self):
        self.assertEqual(planfile.validate(tasks_plan(task("t1", serves=["R1.1"]))), [])
        for bad in ("R1.1", ["R1.1", ""], [1], [None]):
            v = planfile.validate(tasks_plan(task("t1", serves=bad)))
            self.assertTrue(any(".serves" in s for s in v), f"serves={bad!r} must be rejected")


class Coverage(unittest.TestCase):
    def test_covered_plan_is_clean(self):
        plan = tasks_plan(task("t1", serves=["R1.1"]), task("t2", serves=["R1.2", "R2.1"]))
        self.assertEqual(planfile.coverage_violations(plan, ["R1.1", "R1.2", "R2.1"]), [])

    def test_uncovered_requirement_fires(self):
        plan = tasks_plan(task("t1", serves=["R1.1"]))
        v = planfile.coverage_violations(plan, ["R1.1", "R1.2"])
        self.assertEqual(len(v), 1)
        self.assertIn("R1.2", v[0])
        self.assertIn("exhausted", v[0])

    def test_honest_outs_skip_coverage(self):
        exhausted = tasks_plan(disposition="exhausted", tasks=[])
        self.assertEqual(planfile.coverage_violations(exhausted, ["R1.1"]), [])
        fork = tasks_plan(disposition="needs_decision", question="Which?", tasks=[])
        self.assertEqual(planfile.coverage_violations(fork, ["R1.1"]), [])

    def test_dangling_serves_fires_only_with_known_ids(self):
        plan = tasks_plan(task("t1", serves=["R9.9", "R1.1"]))
        self.assertEqual(planfile.coverage_violations(plan, ["R1.1"]), [])
        v = planfile.coverage_violations(plan, ["R1.1"], known_ids={"R1.1", "R1.2"})
        self.assertEqual(len(v), 1)
        self.assertIn("R9.9", v[0])


class DeadMethods(unittest.TestCase):
    def test_dead_method_fires_by_fingerprint_not_wording(self):
        plan = tasks_plan(task("t1", method="Use the REST API!"))
        v = planfile.dead_violations(plan, {planfile.fp("use the rest api")})
        self.assertEqual(len(v), 1)
        self.assertIn("Dead ends", v[0])

    def test_live_methods_are_silent(self):
        plan = tasks_plan(task("t1", method="use grpc"))
        self.assertEqual(planfile.dead_violations(plan, {planfile.fp("use rest")}), [])
        self.assertEqual(planfile.dead_violations(plan, set()), [])


class DodBlock(unittest.TestCase):
    def test_absent_without_dod_ids(self):
        for prompt in (envelope.plan_prompt("I", "/x/plan.json"),
                       envelope.partial_replan_prompt("I", "/x/p.json", set())):
            self.assertNotIn("serves", prompt)
            self.assertNotIn("DEFINITION OF DONE", prompt)

    def test_names_every_unmet_id_and_the_binding_rules(self):
        for prompt in (envelope.plan_prompt("I", "/x/plan.json",
                                            dod_ids=["R1.1", "R2.3"]),
                       envelope.partial_replan_prompt("I", "/x/p.json", {"t1"},
                                                      dod_ids=["R1.1", "R2.3"])):
            self.assertIn("DEFINITION OF DONE", prompt)
            self.assertIn('"serves"', prompt)
            self.assertIn("R1.1", prompt)
            self.assertIn("R2.3", prompt)
            self.assertIn("at least one task", prompt)
            self.assertIn('"exhausted"', prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
