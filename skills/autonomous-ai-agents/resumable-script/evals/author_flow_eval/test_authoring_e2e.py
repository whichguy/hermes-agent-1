"""test_authoring_e2e.py — one parametrized test function; every suite points at it.

This is the whole point of the "multiple suites → same test function" shape: `SCENARIOS` is a dict of
suites, but there is exactly ONE test body. Each scenario becomes a parametrize case tagged with its
suite's marker, so you can run a single suite (`pytest -m L3`) or the whole ladder (`pytest`).
"""
import pytest

from scenarios import SCENARIOS
from driver import run_scenario


def _cases():
    for suite, scenarios in SCENARIOS.items():
        marker = getattr(pytest.mark, suite)           # L1..L6 markers (declared in pytest.ini)
        for sc in scenarios:
            yield pytest.param(sc, id="%s:%s" % (suite, sc["id"]), marks=marker)


@pytest.mark.parametrize("scenario", list(_cases()))
def test_authoring_e2e(scenario, live_env):
    """Real LLM authors the spec → engine runs it → suspends at a human gate → a real LLM answers as
    the human → the flow reaches the expected terminal. All assertions live inside run_scenario."""
    run_scenario(scenario, live_env)
