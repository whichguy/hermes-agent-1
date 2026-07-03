"""bridge.py — run a workflow `agent` step on the REAL, in-container Hermes from the host.

`scripts/workflow.default_agent_caller` assumes `hermes` lives in the same filesystem; here Hermes runs
only inside the `hermes` container and its MCP state server can read files ONLY under the bind-mounted
`~/.hermes` (host) == `/opt/data` (container). This adapter bridges that gap. Two things were established
empirically (see the Phase-B investigation) and drive the design:

  1. `WORKFLOW_STATE_FILE` passed via `docker exec -e` is DROPPED by Hermes' MCP env filter — so the env
     channel that `default_agent_caller` prefers does not work across the boundary.
  2. The state MCP subprocess runs with `$HOME=/opt/data` (HERMES_HOME), so it reads the sentinel
     `/opt/data/.hermes/workflow/active` — which is host-writable as `~/.hermes/.hermes/workflow/active`.
     Pointing the server is therefore a pure host file write; no `docker exec` needed for state at all.

So the bridge: seeds the workflow state into a mounted file, points the server at its CONTAINER path via
the host-writable sentinel, invokes `hermes -z -t all` in the container (via `oneshot.run_docker_exec`),
then RE-READS the mounted file to recover whatever the agent wrote with `set_state` (the mutations-file
env channel is filtered too, so we diff the file instead) and folds those into the returned `set`.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPTS = os.path.join(ROOT, "scripts")
for _p in (SCRIPTS, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from oneshot import run_docker_exec                          # noqa: E402
from workflow import _convo_to_text  # noqa: E402

HOST_HERMES = os.path.join(os.environ.get("HOME", os.path.expanduser("~")), ".hermes")
HOST_EVAL_ROOT = os.path.join(HOST_HERMES, "workflow", "eval")          # -> /opt/data/workflow/eval
CONTAINER_EVAL_ROOT = "/opt/data/workflow/eval"
# The MCP child reads $HOME/.hermes/workflow/active with $HOME=/opt/data, i.e. this host path:
HOST_SENTINEL = os.path.join(HOST_HERMES, ".hermes", "workflow", "active")

CONTAINER = os.environ.get("RESUMABLE_EVAL_CONTAINER", "hermes")
DEFAULT_TIMEOUT = int(os.environ.get("RESUMABLE_EVAL_AGENT_TIMEOUT", "300"))


def _exec_with_retry(prompt, timeout, container, tries=3, **kw):
    """run_docker_exec with a small transient-transport retry (timeouts / docker hiccups / model
    backend stalls kill a whole flow otherwise — on_error covers run/search steps, not callers)."""
    proc = None
    for attempt in range(tries):
        proc = run_docker_exec(prompt, timeout=timeout, container=container, **kw)
        if proc.returncode == 0 and (proc.stdout or "").strip():
            return proc
    return proc


def docker_agent_caller(convo, state, state_dir, model=None, provider=None,
                        toolsets="all", timeout=DEFAULT_TIMEOUT, container=None):
    """The `agent=` callable for load_workflow, run against the real in-container Hermes.

    Signature matches `default_agent_caller(convo, state, state_dir, ...)`. Returns the agent's structured
    reply dict ({"next", "set", "result", ...} or {"ask": ...}), with any live `set_state` writes folded
    into `set`. Raises like the production caller so the interpreter's repair loop can react."""
    container = container or CONTAINER
    model = model or os.environ.get("RESUMABLE_EVAL_MODEL")
    provider = provider or os.environ.get("RESUMABLE_EVAL_PROVIDER")

    key = os.path.basename(state_dir.rstrip("/")) or "run"
    host_dir = os.path.join(HOST_EVAL_ROOT, key)
    os.makedirs(host_dir, exist_ok=True)
    host_state_file = os.path.join(host_dir, "agent_state.json")
    container_state_file = "%s/%s/agent_state.json" % (CONTAINER_EVAL_ROOT, key)

    before = dict(state)
    with open(host_state_file, "w", encoding="utf-8") as f:
        json.dump(before, f, sort_keys=True)
    # point the (single, host-writable) sentinel at THIS run's file, by its container path
    os.makedirs(os.path.dirname(HOST_SENTINEL), exist_ok=True)
    with open(HOST_SENTINEL, "w", encoding="utf-8") as f:
        f.write(container_state_file)

    prompt = _convo_to_text(convo)      # the engine scaffold arrives as the leading "system:" line
    proc = _exec_with_retry(prompt, timeout, container,
                            model=model, provider=provider, toolsets=toolsets)
    if proc.returncode == 124:
        raise RuntimeError("hermes -z timed out after %ss" % timeout)
    if proc.returncode != 0:
        raise RuntimeError("hermes -z failed (%d): %s" % (proc.returncode, (proc.stderr or "")[-2000:]))

    # v2: return the RAW text (the engine owns parsing/repair). Live set_state writes are recovered
    # by diffing the re-read state file (the env mutations channel is filtered by Hermes) and ride
    # the caller-captured {"text", "set"} channel.
    merged = {}
    try:
        with open(host_state_file, encoding="utf-8") as f:
            after = json.load(f)
    except (OSError, ValueError):
        after = before
    for k, v in after.items():
        if k not in before or before[k] != v:
            merged["$.%s" % k] = v
    if merged:
        return {"text": proc.stdout, "set": merged}
    return proc.stdout


def docker_llm_caller(convo, model=None, provider=None, timeout=DEFAULT_TIMEOUT, container=None):
    """The `llm=` callable for load_workflow — a `prompt` step is ONE model call with no tools, so
    unlike the agent caller there is no state plumbing: flatten the convo (the engine scaffold rides
    as the leading "system:" line), run `hermes -z` in the container, and return the RAW text — the
    engine owns parsing, repair, and routing. Doubles as the `router=` judge caller."""
    container = container or CONTAINER
    model = model or os.environ.get("RESUMABLE_EVAL_MODEL")
    provider = provider or os.environ.get("RESUMABLE_EVAL_PROVIDER")
    prompt = _convo_to_text(convo)
    proc = _exec_with_retry(prompt, timeout, container, model=model, provider=provider)
    if proc.returncode == 124:
        raise RuntimeError("hermes -z timed out after %ss" % timeout)
    if proc.returncode != 0:
        raise RuntimeError("hermes -z failed (%d): %s" % (proc.returncode, (proc.stderr or "")[-2000:]))
    return proc.stdout
