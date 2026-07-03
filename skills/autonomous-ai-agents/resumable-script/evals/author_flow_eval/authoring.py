"""authoring.py — ask the real Hermes to author a workflow spec, with a bounded repair loop.

This is the "an LLM writes the script" heart of the eval. It drives `hermes -z` (via the skill's own
`oneshot.run_docker_exec`, i.e. `docker exec <container> hermes -z <prompt>`) with the cheatsheet + the
scenario task, parses the reply with the interpreter's OWN tolerant extractor, and validates it with the
interpreter's OWN `_validate_spec`. If parse/validate fails, it feeds the exact error back and re-prompts
(bounded) — mirroring how the interpreter itself repairs a bad model return (workflow.md §7).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # skills/resumable-script
SCRIPTS = os.path.join(ROOT, "scripts")
for _p in (SCRIPTS, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cheatsheet                                       # noqa: E402  (eval-local)
from oneshot import run_docker_exec                     # noqa: E402  (skill script)
from workflow import _extract_json_object, _validate_spec  # noqa: E402  (skill script)


class AuthoringError(RuntimeError):
    """Raised when the model could not produce a valid spec within the repair budget."""
    def __init__(self, message, attempts):
        super().__init__(message)
        self.attempts = attempts                        # list of dicts: {prompt, raw, error}


def author_spec(task, registry, container="hermes", model=None, provider=None,
                timeout=180, max_repair=2):
    """Author one spec for `task`. Returns (spec_dict, attempts_log).

    attempts_log is a list of {"raw": <hermes stdout>, "error": <validation error or None>} — the eval
    persists it as evidence. Raises AuthoringError if no attempt validated within 1 + max_repair tries.
    """
    attempts = []
    error = None
    for _ in range(1 + max_repair):
        prompt = cheatsheet.author_prompt(task, error=error)
        full = cheatsheet.SYSTEM + "\n\n" + prompt
        proc = run_docker_exec(full, timeout=timeout, container=container,
                               model=model, provider=provider)
        raw = proc.stdout or ""
        rec = {"raw": raw, "returncode": proc.returncode, "error": None}
        if proc.returncode == 124:
            rec["error"] = error = "hermes -z timed out"
            attempts.append(rec)
            continue
        if proc.returncode != 0:
            rec["error"] = error = "hermes -z failed (%d): %s" % (proc.returncode, (proc.stderr or "")[-500:])
            attempts.append(rec)
            continue
        spec = _extract_json_object(raw)
        if spec is None:
            rec["error"] = error = "no JSON object found in the reply"
            attempts.append(rec)
            continue
        try:
            _validate_spec(spec, registry)
        except Exception as e:                           # noqa: BLE001 — surface any validation failure to the model
            rec["error"] = error = "%s: %s" % (type(e).__name__, e)
            attempts.append(rec)
            continue
        rec["spec"] = spec
        attempts.append(rec)
        return spec, attempts
    raise AuthoringError("no valid spec after %d attempt(s): last error = %s"
                         % (len(attempts), error), attempts)
