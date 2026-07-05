#!/usr/bin/env python3
"""Unit tests for the fail-safe operation classifier — deterministic, no network."""

import json
import os
import socket
import sys
import unittest
import urllib.error
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))

from answerer import _DATA_NOTE  # noqa: E402
from opclass import classify_operation  # noqa: E402
import opclass  # noqa: E402


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


def ollama_response(verdict):
    body = {"message": {"content": json.dumps(verdict)}}
    return FakeResponse(json.dumps(body).encode())


def verdict(opclass_name, irreversible=False, touches_irreplaceable=False,
            reason="test verdict"):
    return {
        "opclass": opclass_name,
        "irreversible": irreversible,
        "touches_irreplaceable": touches_irreplaceable,
        "reason": reason,
    }


class TestFailSafe(unittest.TestCase):
    def assertFailSafe(self, result):
        self.assertEqual(result["opclass"], "DESTRUCTIVE")
        self.assertIs(result["irreversible"], True)
        self.assertIs(result["touches_irreplaceable"], True)
        self.assertIs(result["needs_confirmation"], True)
        self.assertIs(result["fail_safe"], True)
        self.assertTrue(result["reason"])
        self.assertTrue(result["reason"].startswith("classifier failed: "))
        self.assertEqual(set(result), {
            "opclass", "irreversible", "touches_irreplaceable", "reason",
            "needs_confirmation", "fail_safe",
        })

    def test_network_error_fails_safe(self):
        for error in (urllib.error.URLError("offline"), socket.timeout("slow")):
            with self.subTest(error=type(error).__name__):
                with mock.patch("opclass.urllib.request.urlopen", side_effect=error):
                    self.assertFailSafe(classify_operation("read it", "task", []))

    def test_malformed_json_fails_safe(self):
        response = FakeResponse(b"this is not JSON")
        with mock.patch("opclass.urllib.request.urlopen", return_value=response):
            self.assertFailSafe(classify_operation("read it", "task", []))

    def test_missing_required_keys_fails_safe(self):
        response = ollama_response({"reason": "whatever"})
        with mock.patch("opclass.urllib.request.urlopen", return_value=response):
            self.assertFailSafe(classify_operation("read it", "task", []))

    def test_invalid_opclass_fails_safe(self):
        response = ollama_response(verdict("SOMETHING_ELSE"))
        with mock.patch("opclass.urllib.request.urlopen", return_value=response):
            self.assertFailSafe(classify_operation("read it", "task", []))

    def test_non_boolean_risk_fields_fail_safe(self):
        response = ollama_response(verdict("MODIFY", irreversible="false"))
        with mock.patch("opclass.urllib.request.urlopen", return_value=response):
            self.assertFailSafe(classify_operation("edit it", "task", []))


class TestGoldenVerdicts(unittest.TestCase):
    def classify(self, model_verdict, question):
        response = ollama_response(model_verdict)
        with mock.patch("opclass.urllib.request.urlopen", return_value=response):
            return classify_operation(question, "Complete the task", ["Known fact"])

    def test_read_only(self):
        result = self.classify(verdict("READ_ONLY"), "Read a file")
        self.assertEqual(result["opclass"], "READ_ONLY")
        self.assertIs(result["irreversible"], False)
        self.assertIs(result["needs_confirmation"], False)
        self.assertIs(result["fail_safe"], False)

    def test_sandbox(self):
        result = self.classify(verdict("SANDBOX"), "Run tests in a scratch copy")
        self.assertEqual(result["opclass"], "SANDBOX")
        self.assertIs(result["needs_confirmation"], False)

    def test_modify(self):
        result = self.classify(verdict("MODIFY"), "Edit a config file")
        self.assertEqual(result["opclass"], "MODIFY")
        self.assertIs(result["irreversible"], False)
        self.assertIs(result["needs_confirmation"], False)

    def test_destructive_irreplaceable(self):
        model_verdict = verdict(
            "DESTRUCTIVE", irreversible=True, touches_irreplaceable=True,
            reason="deletes original photos")
        result = self.classify(model_verdict, "Delete original photos to free disk space")
        self.assertEqual(result["opclass"], "DESTRUCTIVE")
        self.assertIs(result["irreversible"], True)
        self.assertIs(result["touches_irreplaceable"], True)
        self.assertIs(result["needs_confirmation"], True)


class TestDerivedAndRequestBehavior(unittest.TestCase):
    def classify(self, model_verdict):
        with mock.patch(
                "opclass.urllib.request.urlopen",
                return_value=ollama_response(model_verdict)):
            return classify_operation("question", "problem", [])

    def test_irreplaceable_alone_needs_confirmation(self):
        result = self.classify(verdict(
            "MODIFY", irreversible=False, touches_irreplaceable=True))
        self.assertIs(result["needs_confirmation"], True)

    def test_no_risk_flags_needs_no_confirmation(self):
        result = self.classify(verdict(
            "MODIFY", irreversible=False, touches_irreplaceable=False))
        self.assertIs(result["needs_confirmation"], False)

    def test_prompt_contains_injection_guard_and_irreplaceable_rule(self):
        captured = {}

        def respond(req, timeout):
            captured["body"] = json.loads(req.data)
            captured["timeout"] = timeout
            return ollama_response(verdict("READ_ONLY"))

        with mock.patch("opclass.urllib.request.urlopen", side_effect=respond):
            result = classify_operation(
                {"question": "Inspect metadata"}, "Review the library", ["One fact"],
                capability_ceiling="read")

        self.assertIs(result["fail_safe"], False)
        prompt = captured["body"]["messages"][0]["content"]
        self.assertIn(_DATA_NOTE, prompt)
        self.assertIn("<task>\nReview the library\n</task>", prompt)
        self.assertIn("<question>\nInspect metadata\n</question>", prompt)
        self.assertIn("photos, images, or other proprietary/non-reproducible", prompt)
        self.assertIs(captured["body"]["think"], False)
        self.assertEqual(captured["body"]["options"], {
            "temperature": 0.0, "num_predict": 180})

    def test_import_has_no_iterate_dependency(self):
        self.assertIs(classify_operation, opclass.classify_operation)
        self.assertNotIn("iterate", opclass.__dict__)
        self.assertEqual(opclass.__dict__["_parse_json_container"].__module__, "answerer")


if __name__ == "__main__":
    unittest.main()
