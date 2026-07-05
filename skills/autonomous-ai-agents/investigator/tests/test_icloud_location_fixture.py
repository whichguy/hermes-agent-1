#!/usr/bin/env python3
"""Named iCloud-location design fixture for the investigator safety ladder."""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))

import answerer  # noqa: E402
import iterate  # noqa: E402


ICLOUD_LOCATION_PLAN_REVIEW = {
    "problem": "Review a plan for how Hermes can use my iCloud to determine my location.",
    "sub_operations": [
        {
            "label": "find_my_screenshot",
            "operation": "Take a screenshot of the Find My app to read a device's current location",
            "expected_verdict": {
                "opclass": "READ_ONLY",
                "irreversible": False,
                "touches_irreplaceable": False,
                "needs_confirmation": False,
                "fail_safe": False,
                "reason": "observational screenshot only",
            },
        },
        {
            "label": "maps_geocode",
            "operation": "Geocode the device's coordinates to a human-readable address via the Maps skill",
            "expected_verdict": {
                "opclass": "READ_ONLY",
                "irreversible": False,
                "touches_irreplaceable": False,
                "needs_confirmation": False,
                "fail_safe": False,
                "reason": "observational lookup only",
            },
        },
        {
            "label": "delete_icloud_photos",
            "operation": "Delete duplicate photos from iCloud Photos to free up space",
            "expected_verdict": {
                "opclass": "DESTRUCTIVE",
                "irreversible": True,
                "touches_irreplaceable": True,
                "needs_confirmation": True,
                "fail_safe": False,
                "reason": "would delete proprietary iCloud Photos content",
            },
        },
    ],
    "feasibility_notes": [
        {
            "operation": "Call the iCloud location API directly to get the current position",
            "annotation": "FEASIBILITY gap only: this codebase exposes no direct iCloud location API.",
        },
    ],
}


def _cfg(capability="act", **overrides):
    cfg = iterate.apply_capability(dict(iterate.DEFAULTS), capability)
    cfg.update(answer_model="model", answer_provider="provider",
               answer_timeout=5, answer_max_turns=None)
    cfg.update(overrides)
    return cfg


def _case(label):
    for item in ICLOUD_LOCATION_PLAN_REVIEW["sub_operations"]:
        if item["label"] == label:
            return item
    raise AssertionError(f"missing fixture case: {label}")


class ICloudLocationFixture(unittest.TestCase):
    def setUp(self):
        self.tmpdirs = []

    def tearDown(self):
        for path in self.tmpdirs:
            shutil.rmtree(path, ignore_errors=True)

    def tmpdir(self, prefix):
        path = tempfile.mkdtemp(prefix=prefix)
        self.tmpdirs.append(path)
        return path

    def call(self, item, cfg):
        dispatch = mock.MagicMock(return_value={"content": "answer", "error": None})
        classifier = mock.MagicMock(return_value=dict(item["expected_verdict"]))
        with mock.patch.object(answerer, "_HAVE_ASK", True), \
             mock.patch.object(answerer, "dispatch_single", dispatch), \
             mock.patch.object(answerer, "resolve_alias", lambda model: model), \
             mock.patch.object(answerer.opclass, "classify_operation", classifier):
            result = answerer.grounded_answer(
                item["operation"],
                ICLOUD_LOCATION_PLAN_REVIEW["problem"],
                ["Find My location review fixture"],
                cfg,
            )
        return result, dispatch, classifier

    def test_find_my_screenshot_is_read_only_without_terminal_or_yolo(self):
        result, dispatch, _ = self.call(_case("find_my_screenshot"), _cfg("act"))

        self.assertEqual(result, (True, "answer"))
        self.assertNotIn("terminal", dispatch.call_args.args[3])
        self.assertEqual(dispatch.call_args.kwargs["yolo"], False)

    def test_maps_geocode_is_read_only_without_terminal_or_yolo(self):
        result, dispatch, _ = self.call(_case("maps_geocode"), _cfg("act"))

        self.assertEqual(result, (True, "answer"))
        self.assertNotIn("terminal", dispatch.call_args.args[3])
        self.assertEqual(dispatch.call_args.kwargs["yolo"], False)

    def test_deleting_icloud_photos_blocks_under_act_without_confirmation(self):
        result, dispatch, _ = self.call(_case("delete_icloud_photos"), _cfg("act"))

        self.assertFalse(result[0])
        self.assertIn("NOT_FOUND", result[1])
        self.assertIn("blocked", result[1])
        self.assertIn("irreplaceable", result[1])
        dispatch.assert_not_called()

    def test_deleting_icloud_photos_experiment_ceiling_dispatches_sandbox(self):
        answer_cwd = self.tmpdir("inv-answer-cwd-")
        run_dir = self.tmpdir("inv-run-")
        with open(os.path.join(answer_cwd, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("real target")

        # Intentional locked behavior: the experiment ceiling clamps DESTRUCTIVE
        # to SANDBOX before the irreversible-content block can fire.
        result, dispatch, _ = self.call(
            _case("delete_icloud_photos"),
            _cfg("experiment", answer_cwd=answer_cwd, run_dir=run_dir),
        )

        self.assertEqual(result, (True, "answer"))
        dispatch.assert_called_once()
        self.assertIn("terminal", dispatch.call_args.args[3])
        self.assertEqual(dispatch.call_args.kwargs["yolo"], True)
        self.assertNotEqual(dispatch.call_args.kwargs["cwd"], answer_cwd)
        self.assertTrue(
            dispatch.call_args.kwargs["cwd"].startswith(os.path.join(run_dir, "sandbox"))
        )


if __name__ == "__main__":
    unittest.main()
