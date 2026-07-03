#!/usr/bin/env python3
"""oneshot.py — the bare `hermes -z` invocation pattern shared across this skill family.

relentless-solve (task/plan/gate oneshots) and method-explorer (drive.py's tick loop)
independently arrived at the same shape: direct subprocess when the hermes binary is
local (in-container, the normal habitat for both), docker exec otherwise (host-side); and
the same lesson — a timeout may still have advanced state on disk (a result artifact, a
plan-tree) before the process was killed, so return whatever partial output exists rather
than raising, and let the CALLER's own on-disk artifact be the source of truth, never this
return value alone. relentless.py's own docstrings already credit this as "the drive.py
lesson" — this module is where that lesson now lives once, instead of twice.

This is infrastructure/dispatch plumbing, NOT the `ask` skill's `dispatch_single` — that
runs a full `hermes chat -q` multi-turn agent (alias resolution, sessions, thinking-level,
retries). This is a bare, stateless, single-turn `hermes -z` oneshot: a different
invocation mode entirely, used where a caller wants one JSON-emitting turn, not an agent
conversation.

Both functions return a CompletedProcess-shaped object (`.returncode`, `.stdout`,
`.stderr`) so a caller that needs to distinguish a timeout (`returncode == 124`) from a
normal completion can (e.g. livelock/STUCK detection), while a caller that only wants the
final text can just read `.stdout`.

Stdlib only; no env reads beyond what's explicitly passed in.
"""

import subprocess

DEFAULT_HERMES_BIN = "/opt/hermes/bin/hermes"


def _tolerant_run(cmd, timeout):
    """subprocess.run, but a TimeoutExpired becomes a returncode=124 CompletedProcess
    with whatever partial stdout/stderr the process had already produced, instead of a
    raised exception — callers can inspect .returncode without a try/except of their own."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        out, err = e.stdout, e.stderr
        stdout = out.decode(errors="replace") if isinstance(out, bytes) else (out or "")
        stderr = err.decode(errors="replace") if isinstance(err, bytes) else (err or "")
        return subprocess.CompletedProcess(cmd, 124, stdout, stderr)


def _hermes_flags(model, provider, toolsets):
    """The `-m/--provider/-t` tail shared by both dispatch modes (empty when all unset)."""
    flags = []
    if model:
        flags += ["-m", model]
    if provider:
        flags += ["--provider", provider]
    if toolsets:
        flags += ["-t", toolsets]
    return flags


def run_direct(prompt, timeout, hermes_bin=None, pad=0, model=None, provider=None, toolsets=None):
    """One bare `hermes -z` turn via direct subprocess — the in-container habitat both
    relentless-solve and method-explorer run in. `pad` extends the Python-level
    subprocess timeout beyond `timeout` (relentless-solve's convention: give hermes's own
    internal timeout a head start before this call's timeout backstops it; method-
    explorer's drive.py uses pad=0, its own timeout IS the only bound). `model`/`provider`/
    `toolsets` add the matching `-m`/`--provider`/`-t` flags when set."""
    bin_ = hermes_bin or DEFAULT_HERMES_BIN
    cmd = [bin_, "-z", prompt] + _hermes_flags(model, provider, toolsets)
    return _tolerant_run(cmd, timeout + pad)


def run_docker_exec(prompt, timeout, container, hermes_bin=None, pad=0,
                    model=None, provider=None, toolsets=None, env=None):
    """One bare `hermes -z` turn via `docker exec <container> timeout <timeout> ...`
    (host-side dispatch into the container) — the shell-level `timeout` command is the
    primary bound; `pad` extends the Python-level subprocess timeout as a backstop in
    case docker itself hangs beyond that. `model`/`provider`/`toolsets` add the matching
    `-m`/`--provider`/`-t` flags; `env` (a dict) is injected into the container process via
    `docker exec -e K=V ...` — the workflow `agent` bridge uses it to point the in-container
    state MCP server at this run's files (WORKFLOW_STATE_FILE / WORKFLOW_MUTATIONS_FILE)."""
    hermes_bin = hermes_bin or DEFAULT_HERMES_BIN
    cmd = ["docker", "exec"]
    for k, v in (env or {}).items():
        cmd += ["-e", "%s=%s" % (k, v)]
    cmd += [container, "timeout", str(timeout), hermes_bin, "-z", prompt]
    cmd += _hermes_flags(model, provider, toolsets)
    return _tolerant_run(cmd, timeout + pad)
