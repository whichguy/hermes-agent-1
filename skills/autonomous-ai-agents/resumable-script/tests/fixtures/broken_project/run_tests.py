#!/usr/bin/env python3
"""Planted-bug test runner for the real-repo integration.

- Exits 1 with an AssertionError-shaped message because src/calc.py has a wrong operator.
- Raises ModuleNotFoundError when DEP_DOWN=1, so the investigation's `reproduce` step can
  fail on a broken environment and be recovered by resuming.
Fixing src/calc.py (`a - b` -> `a + b`) makes it exit 0.
"""
import os
import sys

if os.environ.get("DEP_DOWN") == "1":
    raise ModuleNotFoundError("No module named 'numpylite'")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.calc import add  # noqa: E402


def main():
    got = add(2, 3)
    if got != 5:
        print("FAIL tests/test_calc.py::test_add - AssertionError in add "
              "(src/calc.py): expected 5, got %d" % got)
        return 1
    print("OK - 1 passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
