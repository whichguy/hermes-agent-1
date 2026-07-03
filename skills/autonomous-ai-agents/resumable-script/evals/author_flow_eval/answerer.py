"""answerer.py — a real LLM plays the human at every suspension.

The authoring model now picks its own gate options, so the driver can't prescribe answers. Instead,
each time the workflow suspends, `answer_gate` hands the rendered question + the gate's options + the
scenario's INTENT (e.g. "approve the request", "ask for one revision first, then approve") + the answers
already given to a real `hermes -z` turn, and deterministically parses the reply back onto one of the
options (exact -> case-insensitive -> unique-substring), retrying once with the parse error. Gates
authored without options accept the reply as free text. Every exchange is returned for the artifacts.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPTS = os.path.join(ROOT, "scripts")
for _p in (SCRIPTS, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from oneshot import run_docker_exec                          # noqa: E402

DEFAULT_TIMEOUT = int(os.environ.get("RESUMABLE_EVAL_ANSWER_TIMEOUT", "120"))


class AnswerError(RuntimeError):
    """The answerer could not produce a usable reply for this gate (fails the attempt)."""


def _build_prompt(pending, intent, history, error=None):
    q = pending.get("question") or {}
    options = q.get("options")
    lines = [
        "You are the human operator of a paused workflow. It has stopped to ask you a question.",
        "",
        "QUESTION: %s" % (q.get("prompt") or "(no question text)"),
        "OPTIONS: %s" % (", ".join(options) if options else "(free text — reply in one short line)"),
    ]
    if history:
        lines.append("YOU HAVE ALREADY ANSWERED (in order): %s" % ", ".join(repr(h) for h in history))
    lines += [
        "YOUR GOAL: %s" % intent,
        "",
        "Reply with EXACTLY one of the options — the option text alone, nothing else."
        if options else "Reply with one short line of text, nothing else.",
    ]
    if error:
        lines.append("Your previous reply was rejected: %s. Answer again, options text only." % error)
    return "\n".join(lines)


def _match_option(reply, options):
    """Deterministically map the model's reply onto one option, or return (None, reason)."""
    text = (reply or "").strip().strip('"').strip("'").rstrip(".")
    if not text:
        return None, "empty reply"
    for o in options:
        if text == o:
            return o, None
    low = text.lower()
    ci = [o for o in options if o.lower() == low]
    if len(ci) == 1:
        return ci[0], None
    sub = [o for o in options if o.lower() in low or low in o.lower()]
    if len(sub) == 1:
        return sub[0], None
    return None, "reply %r matches %d options" % (text[:80], len(sub))


def answer_gate(pending, intent, history, container="hermes", model=None, provider=None,
                timeout=DEFAULT_TIMEOUT):
    """Answer one suspension. Returns (answer, transcript) — `answer` is the exact option text (or the
    free-text line), `transcript` is a list of {prompt, raw, answer|error} for the artifacts. Raises
    AnswerError when no usable reply emerges within one retry."""
    model = model or os.environ.get("RESUMABLE_EVAL_MODEL")
    provider = provider or os.environ.get("RESUMABLE_EVAL_PROVIDER")
    options = (pending.get("question") or {}).get("options")
    transcript = []
    error = None
    for _ in range(2):                                       # one attempt + one repair
        prompt = _build_prompt(pending, intent, history, error=error)
        proc = run_docker_exec(prompt, timeout=timeout, container=container,
                               model=model, provider=provider)
        raw = (proc.stdout or "").strip()
        rec = {"prompt": prompt, "raw": raw}
        if proc.returncode != 0:
            rec["error"] = error = "hermes -z failed (%d)" % proc.returncode
            transcript.append(rec)
            continue
        if not options:
            line = raw.splitlines()[0].strip() if raw else ""
            if line:
                rec["answer"] = line
                transcript.append(rec)
                return line, transcript
            rec["error"] = error = "empty reply"
            transcript.append(rec)
            continue
        answer, why = _match_option(raw, options)
        if answer is not None:
            rec["answer"] = answer
            transcript.append(rec)
            return answer, transcript
        rec["error"] = error = why
        transcript.append(rec)
    raise AnswerError("gate unanswered after retry: %s (options=%s)" % (error, options))
