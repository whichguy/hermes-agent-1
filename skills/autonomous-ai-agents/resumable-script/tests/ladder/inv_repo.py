"""Ladder bridge: drive examples/investigate_repo from the ladder harness.

Kept deliberately thin — the flow + observer live in examples/ so the hermetic `inv_*`
rungs and the real-repo integration exercise the SAME code. Rungs select a fixture and
scenario purely via env (INVESTIGATE_MODE=fixture, INVESTIGATE_FIXTURE, INVESTIGATE_DEP_DOWN,
INVESTIGATE_CRASH_APPLY).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "examples"))
from investigate_repo import investigate as flow, observer  # noqa: E402,F401
