#!/usr/bin/env python3
"""Unit tests for investigator backup snapshots."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))

import backup  # noqa: E402


class BackupSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="inv-backup-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_snapshot_copies_directory_and_writes_manifest(self):
        target = os.path.join(self.tmp, "target")
        root = os.path.join(self.tmp, "backups")
        os.makedirs(target)
        with open(os.path.join(target, "file.txt"), "w", encoding="utf-8") as fh:
            fh.write("content")

        rec = backup.snapshot(target, root, "modify-question")

        self.assertEqual(rec["original"], os.path.abspath(target))
        self.assertTrue(rec["backup_path"])
        self.assertTrue(os.path.exists(os.path.join(rec["backup_path"], "file.txt")))
        with open(os.path.join(root, "manifest.jsonl"), encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["original"], os.path.abspath(target))
        self.assertEqual(lines[0]["backup_path"], rec["backup_path"])
        self.assertIn("ts", lines[0])
        self.assertEqual(lines[0]["op"], "modify-question")

    def test_missing_target_returns_none(self):
        root = os.path.join(self.tmp, "backups")
        self.assertIsNone(backup.snapshot(None, root, "op"))
        self.assertIsNone(backup.snapshot(os.path.join(self.tmp, "missing"), root, "op"))
        self.assertFalse(os.path.exists(root))

    def test_copy_failure_returns_error_and_manifest(self):
        target = os.path.join(self.tmp, "target")
        root = os.path.join(self.tmp, "backups")
        os.makedirs(target)
        with mock.patch.object(backup.shutil, "copytree", side_effect=OSError("boom")):
            rec = backup.snapshot(target, root, "op")

        self.assertIsNone(rec["backup_path"])
        self.assertIn("boom", rec["error"])
        with open(os.path.join(root, "manifest.jsonl"), encoding="utf-8") as fh:
            line = json.loads(fh.readline())
        self.assertIsNone(line["backup_path"])
        self.assertIn("boom", line["error"])


if __name__ == "__main__":
    unittest.main()
