"""env_setup.py — pytest-free environment probes + one-time container setup.

Kept separate from conftest.py so BOTH the live_env fixture and the test body can call these (a
parametrized test argument like `scenario` isn't reachable from a fixture, so the test itself decides
whether an agent scenario needs the MCP set up). No pytest imports here — callers translate a returned
reason string into pytest.skip.
"""
import os
import shutil
import subprocess
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPTS = os.path.join(ROOT, "scripts")
CONTAINER_STATE_MCP = "/opt/data/workflow/state_mcp.py"     # container view of ~/.hermes/workflow/...
HERMES_BIN = "/opt/hermes/bin/hermes"                        # `hermes` is NOT on the container PATH


def container_up(name):
    try:
        out = subprocess.run(["docker", "ps", "--filter", "name=%s" % name, "--format", "{{.Names}}"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return name in (out.stdout or "").split()


def ollama_up(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return resp.status == 200
    except Exception:                                        # noqa: BLE001 — any network error == down
        return False


def _state_registered(container):
    """True iff a server literally named `state` shows up in `hermes mcp list`."""
    out = subprocess.run(["docker", "exec", container, HERMES_BIN, "mcp", "list"],
                         capture_output=True, text=True, timeout=45)
    for line in (out.stdout or "").splitlines():
        if line.strip().split()[:1] == ["state"]:
            return True
    return False


def ensure_state_mcp(container):
    """Idempotently register scripts/state_mcp.py as the container's `state` MCP server.

    ~/.hermes is bind-mounted to /opt/data, so we copy the server there (host-visible + container-visible)
    and register it by its container path. `hermes mcp add` is INTERACTIVE (it asks to enable the tools),
    so we feed `y` on stdin via `docker exec -i`. Returns None on success, else a reason string to skip."""
    home = os.environ.get("HOME", os.path.expanduser("~"))
    host_dir = os.path.join(home, ".hermes", "workflow")
    os.makedirs(host_dir, exist_ok=True)
    shutil.copyfile(os.path.join(SCRIPTS, "state_mcp.py"), os.path.join(host_dir, "state_mcp.py"))
    try:
        if _state_registered(container):
            return None
        subprocess.run(["docker", "exec", "-i", container, HERMES_BIN, "mcp", "add", "state",
                        "--command", "python3", "--args", CONTAINER_STATE_MCP],
                       input="y\n", capture_output=True, text=True, timeout=60)
        if _state_registered(container):
            return None
    except (OSError, subprocess.SubprocessError) as e:
        return "state MCP registration errored: %s" % e
    return "state MCP did not register (check `docker exec %s %s mcp list`)" % (container, HERMES_BIN)
