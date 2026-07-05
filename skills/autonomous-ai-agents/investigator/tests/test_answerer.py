#!/usr/bin/env python3
"""Safety-ladder tests for investigator answer dispatch."""

import os
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))

import answerer  # noqa: E402
import iterate  # noqa: E402


def verdict(opclass_name, irreversible=False, touches_irreplaceable=False,
            reason="test verdict", fail_safe=False):
    return {
        "opclass": opclass_name,
        "irreversible": irreversible,
        "touches_irreplaceable": touches_irreplaceable,
        "reason": reason,
        "needs_confirmation": irreversible or touches_irreplaceable,
        "fail_safe": fail_safe,
    }


class SafetyLadder(unittest.TestCase):
    def setUp(self):
        self.tmpdirs = []

    def tearDown(self):
        for path in self.tmpdirs:
            shutil.rmtree(path, ignore_errors=True)

    def tmpdir(self, prefix):
        path = tempfile.mkdtemp(prefix=prefix)
        self.tmpdirs.append(path)
        return path

    def cfg(self, capability="act", **over):
        cfg = iterate.apply_capability(dict(iterate.DEFAULTS), capability)
        cfg.update(answer_model="model", answer_provider="provider",
                   answer_timeout=5, answer_max_turns=None)
        cfg.update(over)
        return cfg

    def call(self, classifier_result, cfg, question="question", dispatch_result=None):
        ds = mock.MagicMock(return_value=dispatch_result or {"content": "answer", "error": None})
        classifier = mock.MagicMock(return_value=classifier_result)
        with mock.patch.object(answerer, "_HAVE_ASK", True), \
             mock.patch.object(answerer, "dispatch_single", ds), \
             mock.patch.object(answerer, "resolve_alias", lambda model: model), \
             mock.patch.object(answerer.opclass, "classify_operation", classifier):
            result = answerer.grounded_answer(question, "problem", ["fact"], cfg)
        return result, ds, classifier

    def test_read_only_verdict_removes_terminal(self):
        result, ds, _ = self.call(verdict("READ_ONLY"), self.cfg("act"))
        self.assertEqual(result, (True, "answer"))
        self.assertNotIn("terminal", ds.call_args.args[3])
        self.assertEqual(ds.call_args.kwargs["yolo"], False)

    def test_read_ceiling_clamps_modify_to_read_only_without_backup(self):
        with mock.patch.object(answerer.backup, "snapshot") as snap:
            result, ds, _ = self.call(verdict("MODIFY"), self.cfg("read"))
        self.assertEqual(result, (True, "answer"))
        self.assertNotIn("terminal", ds.call_args.args[3])
        snap.assert_not_called()

    def test_experiment_ceiling_clamps_destructive_to_sandbox(self):
        answer_cwd = self.tmpdir("inv-answer-cwd-")
        run_dir = self.tmpdir("inv-run-")
        with open(os.path.join(answer_cwd, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("real target")
        cfg = self.cfg(
            "experiment", answer_cwd=answer_cwd, run_dir=run_dir,
            confirm_callback=lambda _: (_ for _ in ()).throw(AssertionError("confirmed")))
        with mock.patch.object(answerer.backup, "snapshot") as snap:
            result, ds, _ = self.call(
                verdict("DESTRUCTIVE", irreversible=True, touches_irreplaceable=True), cfg)
        self.assertEqual(result, (True, "answer"))
        self.assertIn("terminal", ds.call_args.args[3])
        self.assertNotEqual(ds.call_args.kwargs["cwd"], answer_cwd)
        self.assertTrue(ds.call_args.kwargs["cwd"].startswith(os.path.join(run_dir, "sandbox")))
        self.assertEqual(ds.call_args.kwargs["yolo"], True)
        snap.assert_not_called()

    def test_prepare_sandbox_uses_real_git_worktree_for_git_repo(self):
        if shutil.which("git") is None:
            self.skipTest("git is not available")
        answer_cwd = self.tmpdir("inv-answer-git-")
        run_dir = self.tmpdir("inv-run-")
        subprocess.run(["git", "init"], cwd=answer_cwd, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        with open(os.path.join(answer_cwd, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("real target\n")
        subprocess.run(["git", "add", "README.md"], cwd=answer_cwd, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        subprocess.run([
            "git", "-c", "user.name=Investigator Test",
            "-c", "user.email=investigator-test@example.invalid",
            "commit", "-m", "initial",
        ], cwd=answer_cwd, check=True, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, text=True)

        sandbox_path = answerer._prepare_sandbox(answer_cwd, run_dir, "git-worktree")

        self.assertIsNotNone(sandbox_path)
        worktrees = subprocess.run(["git", "worktree", "list"], cwd=answer_cwd,
                                   check=True, capture_output=True, text=True)
        self.assertIn(os.path.abspath(sandbox_path), worktrees.stdout)

    def test_destructive_reversible_is_modify_with_backup_and_no_yolo(self):
        answer_cwd = self.tmpdir("inv-answer-cwd-")
        cfg = self.cfg("act", answer_cwd=answer_cwd)
        snap = {"original": answer_cwd, "backup_path": "/tmp/backup", "ts": "ts", "op": "op"}
        with mock.patch.object(answerer.backup, "snapshot", return_value=snap) as snapshot:
            result, ds, _ = self.call(verdict("DESTRUCTIVE", irreversible=False), cfg)
        self.assertEqual(result, (True, "answer"))
        snapshot.assert_called_once()
        self.assertEqual(ds.call_args.kwargs["cwd"], answer_cwd)
        self.assertEqual(ds.call_args.kwargs["yolo"], False)
        self.assertIn("/tmp/backup", ds.call_args.args[1])

    def test_irreversible_irreplaceable_without_confirmation_blocks(self):
        result, ds, _ = self.call(
            verdict("DESTRUCTIVE", irreversible=True, touches_irreplaceable=True),
            self.cfg("act"), question="delete original photos")
        self.assertFalse(result[0])
        self.assertIn("NOT_FOUND", result[1])
        self.assertIn("blocked", result[1])
        self.assertIn("irreplaceable", result[1])
        ds.assert_not_called()

    def test_irreversible_without_confirmation_blocks_as_unconfirmed(self):
        result, ds, _ = self.call(
            verdict("DESTRUCTIVE", irreversible=True, touches_irreplaceable=False),
            self.cfg("act"), question="drop database")
        self.assertFalse(result[0])
        self.assertIn("unconfirmed", result[1])
        self.assertNotIn("irreplaceable", result[1])
        ds.assert_not_called()

    def test_irreversible_confirmed_proceeds_as_modify(self):
        cfg = self.cfg("act", confirm_callback=lambda prompt: "drop database" in prompt)
        with mock.patch.object(answerer.backup, "snapshot", return_value=None):
            result, ds, _ = self.call(
                verdict("DESTRUCTIVE", irreversible=True, reason="drops db"), cfg,
                question="drop database")
        self.assertEqual(result, (True, "answer"))
        self.assertEqual(ds.call_args.kwargs["yolo"], False)

    def test_broken_confirm_callback_denies(self):
        def broken(_prompt):
            raise RuntimeError("callback failed")

        result, ds, _ = self.call(
            verdict("DESTRUCTIVE", irreversible=True),
            self.cfg("act", confirm_callback=broken))
        self.assertFalse(result[0])
        ds.assert_not_called()

    def test_escape_hatch_preserves_static_dispatch_without_yolo(self):
        cfg = self.cfg("act", safety_ladder=False)
        cfg["answer_toolsets"] = "custom-tools"
        cfg["answer_directive"] = "STATIC DIRECTIVE"
        ds = mock.MagicMock(return_value={"content": "answer", "error": None})
        classifier = mock.MagicMock(return_value=verdict("READ_ONLY"))
        with mock.patch.object(answerer, "_HAVE_ASK", True), \
             mock.patch.object(answerer, "dispatch_single", ds), \
             mock.patch.object(answerer, "resolve_alias", lambda model: model), \
             mock.patch.object(answerer.opclass, "classify_operation", classifier):
            result = answerer.grounded_answer("question", "problem", [], cfg)
        self.assertEqual(result, (True, "answer"))
        classifier.assert_not_called()
        self.assertEqual(ds.call_args.args[3], "custom-tools")
        self.assertIn("STATIC DIRECTIVE", ds.call_args.args[1])
        self.assertNotIn("yolo", ds.call_args.kwargs)

    def test_classifier_fail_safe_blocks(self):
        result, ds, _ = self.call(
            verdict("DESTRUCTIVE", irreversible=True, touches_irreplaceable=True,
                    reason="classifier failed: offline", fail_safe=True),
            self.cfg("act"))
        self.assertFalse(result[0])
        self.assertIn("irreplaceable", result[1])
        ds.assert_not_called()

    def test_failed_backup_does_not_block_modify_dispatch(self):
        cfg = self.cfg("act", answer_cwd=self.tmpdir("inv-answer-cwd-"))
        snap = {"original": cfg["answer_cwd"], "backup_path": None, "ts": "ts",
                "op": "op", "error": "copy failed"}
        with mock.patch.object(answerer.backup, "snapshot", return_value=snap):
            result, ds, _ = self.call(verdict("MODIFY"), cfg)
        self.assertEqual(result, (True, "answer"))
        self.assertEqual(ds.call_args.kwargs["cwd"], cfg["answer_cwd"])
        self.assertIn("copy failed", ds.call_args.args[1])

    def test_modify_tier_takes_real_backup_through_grounded_answer(self):
        answer_cwd = self.tmpdir("inv-answer-cwd-")
        run_dir = self.tmpdir("inv-run-")
        original_file = os.path.join(answer_cwd, "data.txt")
        original_text = "important local content\n"
        with open(original_file, "w", encoding="utf-8") as fh:
            fh.write(original_text)
        cfg = self.cfg("act", answer_cwd=answer_cwd, run_dir=run_dir)
        ds = mock.MagicMock(return_value={"content": "answer", "error": None})
        classifier = mock.MagicMock(return_value=verdict("MODIFY", irreversible=False))

        with mock.patch.object(answerer, "_HAVE_ASK", True), \
             mock.patch.object(answerer, "dispatch_single", ds), \
             mock.patch.object(answerer, "resolve_alias", lambda model: model), \
             mock.patch.object(answerer.opclass, "classify_operation", classifier):
            result = answerer.grounded_answer("modify local file", "problem", ["fact"], cfg)

        self.assertEqual(result, (True, "answer"))
        backup_root = os.path.join(run_dir, "backups")
        manifest_path = os.path.join(backup_root, "manifest.jsonl")
        self.assertTrue(os.path.isdir(backup_root))
        self.assertTrue(os.path.exists(manifest_path))
        with open(manifest_path, encoding="utf-8") as fh:
            entries = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["original"], os.path.abspath(answer_cwd))
        self.assertEqual(entry["op"], answerer.fp("modify local file"))
        self.assertIn("ts", entry)
        self.assertTrue(entry["backup_path"])
        self.assertNotIn("error", entry)
        with open(os.path.join(entry["backup_path"], "data.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), original_text)


if __name__ == "__main__":
    unittest.main()
