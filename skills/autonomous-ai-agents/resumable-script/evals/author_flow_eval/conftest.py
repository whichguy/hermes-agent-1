"""conftest.py — live-environment gating for the authoring eval.

This eval drives a REAL Hermes + Ollama backend, so it can only run where both are up. `live_env` probes
for them and SKIPS (never fails) when absent — so an offline checkout is a no-op, not a wall of red. The
one-time state-MCP setup that agent scenarios need lives in env_setup.py and is invoked from the test
body (a parametrized arg isn't reachable from a fixture).
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import env_setup                                            # noqa: E402

CONTAINER = os.environ.get("RESUMABLE_EVAL_CONTAINER", "hermes")
OLLAMA_TAGS = os.environ.get("RESUMABLE_EVAL_OLLAMA", "http://localhost:11434/api/tags")
ARTIFACTS = os.path.join(HERE, "artifacts")


@pytest.fixture(scope="session")
def live_env():
    if not env_setup.container_up(CONTAINER):
        pytest.skip("Hermes container %r not running (docker ps)" % CONTAINER)
    if not env_setup.ollama_up(OLLAMA_TAGS):
        pytest.skip("Ollama backend not reachable at %s" % OLLAMA_TAGS)
    # the authoring model chooses its own step kinds, so any scenario may need the agent
    # tools — ensure the state MCP server is registered up front (idempotent, ~1s when present)
    reason = env_setup.ensure_state_mcp(CONTAINER)
    if reason:
        pytest.skip(reason)
    os.makedirs(ARTIFACTS, exist_ok=True)
    return {
        "container": CONTAINER,
        "artifacts": ARTIFACTS,
        "model": os.environ.get("RESUMABLE_EVAL_MODEL"),
        "provider": os.environ.get("RESUMABLE_EVAL_PROVIDER"),
    }
