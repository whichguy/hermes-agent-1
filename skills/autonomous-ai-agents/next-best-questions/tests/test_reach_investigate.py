"""Tests for the opt-in nbq-reach-investigate objective-eval arm."""

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "evals"))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))

try:
    import outcome_bank
    import outcome_eval
    import pipeline
    _OK = True
except Exception:  # pragma: no cover
    _OK = False


@unittest.skipUnless(_OK, "skill scripts not importable")
class TestMockInvestigator(unittest.TestCase):
    def _agentic_task(self):
        return {
            "id": "fixture-task",
            "kind": "script",
            "category": "environment",
            "ambiguous_prompt": "Read the configured database URL.",
            "hidden_spec": "Prefer config.ini over DB_URL.",
            "fixture": {"config.ini": "[db]\nurl = postgres://cfg\n"},
            "checks": ["SENTINEL_NEVER_USED"],
            "ambiguity": ["env wins", "file wins"],
        }

    def test_resolves_fixture_observable_and_refuses_unobservable(self):
        task = self._agentic_task()
        with mock.patch.object(pipeline, "raw_chat",
                               return_value={"content": "postgres://cfg", "error": None}):
            got = outcome_eval.mock_investigator(task, "What URL is configured?", "m")
        self.assertEqual(got, {"question": "What URL is configured?",
                               "answer": "postgres://cfg", "revealed": True})

        with mock.patch.object(pipeline, "raw_chat",
                               return_value={"content": outcome_eval.NO_ANSWER,
                                             "error": None}):
            got = outcome_eval.mock_investigator(task, "What is the prod password?", "m")
        self.assertEqual(got, {"question": "What is the prod password?",
                               "answer": outcome_eval.NO_ANSWER, "revealed": False})

    def test_prompt_does_not_include_oracle_sentinel(self):
        task = self._agentic_task()
        sentinel = "SENTINEL_QK7X9"
        task["checks"] = [f"stdout.strip() == '{sentinel}'"]
        prompt = outcome_eval.investigator_prompt(task, "What URL is configured?")
        self.assertNotIn(sentinel, prompt)
        self.assertIn("postgres://cfg", prompt)

    def test_no_fixture_noops_without_model_call(self):
        task = {
            "id": "micro-like",
            "category": "format",
            "ambiguous_prompt": "Do the thing.",
            "hidden_spec": "Use the short format.",
            "ambiguity": ["short", "long"],
        }
        with mock.patch.object(pipeline, "raw_chat") as raw:
            got = outcome_eval.mock_investigator(task, "What files exist?", "m")
        raw.assert_not_called()
        self.assertEqual(got, {"question": "What files exist?",
                               "answer": outcome_eval.NO_ANSWER, "revealed": False})


@unittest.skipUnless(_OK, "skill scripts not importable")
class TestReachInvestigateArm(unittest.TestCase):
    def _models(self):
        return {"skill": "skill", "solver": "solver", "sim": "sim"}

    def test_nbq_arm_is_inert_by_default(self):
        task = outcome_bank.TASKS[0]
        expected_qa = [
            {"question": "q0", "answer": "A0", "revealed": True},
            {"question": "q1", "answer": outcome_eval.NO_ANSWER, "revealed": False},
        ]

        def fake_sim(_spec, question, _model):
            return dict(expected_qa[0] if question == "q0" else expected_qa[1])

        solved = {"code": "code", "frac": 0.5, "per_test": [True, False]}
        with mock.patch.object(outcome_eval, "questions_nbq",
                               return_value=(["q0", "q1"], {"q_values": [0.8, 0.4]})), \
             mock.patch.object(outcome_eval, "simulate_user", side_effect=fake_sim), \
             mock.patch.object(outcome_eval, "mock_investigator") as investigator, \
             mock.patch.object(outcome_eval, "solve_and_score", return_value=solved):
            row = outcome_eval.run_cell(task, "nbq", 2, self._models())

        investigator.assert_not_called()
        self.assertEqual(row["qa"], expected_qa)
        self.assertEqual(row["revealed"], 1)
        self.assertEqual(row["unanswerable"], 1)
        self.assertEqual(row["frac"], 0.5)
        self.assertEqual(row["per_test"], [True, False])

    def test_arm_plumbing_matches_nbq_and_unknown_arm_still_errors(self):
        task = outcome_bank.BY_ID["db-config"]
        solved = {"code": "", "frac": 1.0, "per_test": []}
        with mock.patch.object(outcome_eval, "questions_nbq",
                               return_value=(["q"], {"usage": {}})) as nbq, \
             mock.patch.object(outcome_eval, "simulate_user",
                               return_value={"question": "q", "answer": "a",
                                             "revealed": True}), \
             mock.patch.object(outcome_eval, "solve_and_score", return_value=solved):
            row = outcome_eval.run_cell(task, "nbq-reach-investigate", 1, self._models(),
                                        max_rounds=4)
        self.assertEqual(row["arm"], "nbq-reach-investigate")
        nbq.assert_called_with(task, 1, "skill", max_rounds=4)

        with self.assertRaisesRegex(ValueError, "unknown arm"):
            outcome_eval.run_cell(task, "no-such-arm", 1, self._models())

    def test_reach_investigate_replaces_only_resolved_unrevealed_entries(self):
        task = outcome_bank.BY_ID["db-config"]
        simulated = [
            {"question": "What URL is in config.ini?", "answer": outcome_eval.NO_ANSWER,
             "revealed": False},
            {"question": "Which source wins?", "answer": "The file wins.", "revealed": True},
        ]
        investigated = {"question": "What URL is in config.ini?",
                        "answer": "postgres://cfg", "revealed": True}
        solved = {"code": "", "frac": 1.0, "per_test": [True]}

        with mock.patch.object(outcome_eval, "questions_nbq",
                               return_value=([x["question"] for x in simulated], {})), \
             mock.patch.object(outcome_eval, "simulate_user",
                               side_effect=[dict(x) for x in simulated]), \
             mock.patch.object(outcome_eval, "mock_investigator",
                               return_value=dict(investigated)) as investigator, \
             mock.patch.object(outcome_eval, "solve_and_score", return_value=solved):
            row = outcome_eval.run_cell(task, "nbq-reach-investigate", 2, self._models())

        investigator.assert_called_once_with(task, simulated[0]["question"], "sim")
        self.assertEqual(row["qa"][0], {"question": simulated[0]["question"],
                                        "answer": "postgres://cfg", "revealed": True,
                                        "resolved_by": "investigator"})
        self.assertEqual(row["qa"][1], simulated[1])
        self.assertEqual(row["revealed"], 2)
        self.assertEqual(row["unanswerable"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
