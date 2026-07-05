#!/usr/bin/env python3
"""Backup snapshots for investigator answer operations."""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil


def _label(text):
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(text or "operation")).strip("-")
    return clean[:80] or "operation"


def _append_manifest(backup_root, rec):
    try:
        os.makedirs(backup_root, exist_ok=True)
        with open(os.path.join(backup_root, "manifest.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
    except Exception:
        pass


def _is_inside(path, root):
    try:
        path_abs = os.path.abspath(path)
        root_abs = os.path.abspath(root)
        return os.path.commonpath([path_abs, root_abs]) == root_abs
    except (OSError, ValueError):
        return False


def snapshot(target_dir, backup_root, op_label) -> dict | None:
    """Copy target_dir to a timestamped location under backup_root.

    Return {"original": ..., "backup_path": ..., "ts": ..., "op": ...}, or None if
    target_dir is falsy / doesn't exist. Copy failures are returned and logged instead
    of raised so a failed backup cannot block the operation.
    """
    if not target_dir or not os.path.isdir(target_dir):
        return None
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    rec = {
        "original": os.path.abspath(target_dir),
        "backup_path": None,
        "ts": ts,
        "op": op_label,
    }
    try:
        os.makedirs(backup_root, exist_ok=True)
        backup_path = os.path.join(backup_root, f"{_label(op_label)}-{ts}")
        if _is_inside(backup_path, target_dir):
            raise ValueError("backup destination is inside target_dir")
        # v1 intentionally copies everything; exclusion filtering can be added when needed.
        shutil.copytree(target_dir, backup_path)
        rec["backup_path"] = backup_path
    except Exception as exc:
        rec["error"] = str(exc)[:200] or exc.__class__.__name__
    _append_manifest(backup_root, rec)
    return rec
