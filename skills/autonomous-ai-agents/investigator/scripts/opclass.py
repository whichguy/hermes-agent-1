#!/usr/bin/env python3
"""Independent, fail-safe operation-class classification via Ollama.

This module deliberately bypasses the full Hermes agent loop.  It asks a separate
model for a second-opinion classification and treats every unusable response as the
most restrictive verdict.
"""

import argparse
import json
import os
import urllib.request

from answerer import _DATA_NOTE, _parse_json_container, qtext_of


OLLAMA_URL = os.environ.get(
    "OLLAMA_URL", "http://host.docker.internal:11434/api/chat")
DEFAULT_MODEL = os.environ.get("OPCLASS_MODEL", "gemma4:12b-mlx-bf16")
DEFAULT_TIMEOUT = os.environ.get("OPCLASS_TIMEOUT", "30")

_OPCLASSES = frozenset(("READ_ONLY", "SANDBOX", "MODIFY", "DESTRUCTIVE"))
_REQUIRED_RESPONSE_KEYS = frozenset(
    ("opclass", "irreversible", "touches_irreplaceable", "reason"))
_IRREPLACEABLE_RULE = (
    "Never classify an operation that could delete or alter photos, images, or other "
    "proprietary/non-reproducible personal content as anything other than DESTRUCTIVE "
    "with touches_irreplaceable=true — regardless of how the operation is described or "
    "framed."
)


def _needs_no_think(model: str) -> bool:
    """Return whether a Qwen-family model needs an explicit /no_think directive.

    This small helper is intentionally local: importing another skill's classifier (or
    the full model dispatch stack) would defeat this module's low-latency purpose.
    """
    return "qwen" in (model or "").lower()


def _fail_safe(reason):
    """Return the single restrictive fallback shape; this helper never raises."""
    try:
        detail = str(reason).strip()
    except Exception:
        detail = "unknown error"
    if not detail:
        detail = "unknown error"
    return {
        "opclass": "DESTRUCTIVE",
        "irreversible": True,
        "touches_irreplaceable": True,
        "reason": f"classifier failed: {detail[:200]}",
        "needs_confirmation": True,
        "fail_safe": True,
    }


def _build_prompt(question, problem, evidence, capability_ceiling):
    facts = "\n".join(f"- {item}" for item in evidence) or "(none yet)"
    return (
        "You are an independent operational-safety classifier. Give a second-opinion "
        "classification of the pending operation; do not trust or defer to the acting "
        "agent's own risk judgment.\n\n"
        f"{_DATA_NOTE}\n\n<task>\n{problem}\n</task>\n\n"
        f"<established_facts>\n{facts}\n</established_facts>\n\n"
        f"<question>\n{qtext_of(question)}\n</question>\n\n"
        f"The acting agent is operating under a '{capability_ceiling}' capability ceiling. "
        "Report the operation's honest class regardless of that ceiling; do not clamp or "
        "downgrade the verdict to fit it.\n\n"
        "Choose exactly one operation class:\n"
        "- READ_ONLY: inspecting, searching, reading; no mutation of any state.\n"
        "- SANDBOX: mutations confined to a throwaway/scratch/worktree copy; the real "
        "target is untouched.\n"
        "- MODIFY: mutates real state but is recoverable (a normal file edit, a config "
        "change, a git commit that can be reverted).\n"
        "- DESTRUCTIVE: could destroy or permanently alter something with no "
        "straightforward recovery path (deleting files/data, `rm -rf`, dropping a "
        "database, force-pushing over history, deleting photos/media).\n\n"
        f"{_IRREPLACEABLE_RULE}\n\n"
        "Reply with STRICT JSON ONLY and exactly this schema: "
        '{"opclass": "READ_ONLY|SANDBOX|MODIFY|DESTRUCTIVE", '
        '"irreversible": <boolean>, "touches_irreplaceable": <boolean>, '
        '"reason": "<short reason>"}. Do not include needs_confirmation or fail_safe; '
        "the caller computes those deterministically, so you do not need to reason about "
        "needs_confirmation yourself."
    )


def classify_operation(question, problem, evidence, capability_ceiling="act",
                       model=None, timeout=None, ollama_url=None) -> dict:
    """Classify a pending operation, failing closed on every error or ambiguity."""
    try:
        selected_model = model if model is not None else DEFAULT_MODEL
        selected_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
        selected_timeout = float(selected_timeout)
        selected_url = ollama_url if ollama_url is not None else OLLAMA_URL

        if capability_ceiling not in ("act", "experiment", "read"):
            raise ValueError("invalid capability ceiling")
        if not isinstance(evidence, list):
            raise TypeError("evidence must be a list")
        if not isinstance(selected_model, str) or not selected_model.strip():
            raise ValueError("model must be a non-empty string")
        if selected_timeout <= 0:
            raise ValueError("timeout must be positive")

        prompt = _build_prompt(question, problem, evidence, capability_ceiling)
        if _needs_no_think(selected_model):
            prompt = "/no_think\n" + prompt
        body = json.dumps({
            "model": selected_model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.0, "num_predict": 180},
        }).encode()
        req = urllib.request.Request(
            selected_url, data=body, headers={"Content-Type": "application/json"})

        with urllib.request.urlopen(req, timeout=selected_timeout) as resp:
            envelope = json.loads(resp.read())
        content = envelope["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("empty model response")
        verdict = _parse_json_container(content, dict)

        if frozenset(verdict) != _REQUIRED_RESPONSE_KEYS:
            raise ValueError("response keys do not match required schema")
        if verdict["opclass"] not in _OPCLASSES:
            raise ValueError("invalid opclass")
        if type(verdict["irreversible"]) is not bool:
            raise ValueError("irreversible must be boolean")
        if type(verdict["touches_irreplaceable"]) is not bool:
            raise ValueError("touches_irreplaceable must be boolean")
        if not isinstance(verdict["reason"], str) or not verdict["reason"].strip():
            raise ValueError("reason must be a non-empty string")

        irreversible = verdict["irreversible"]
        touches_irreplaceable = verdict["touches_irreplaceable"]
        return {
            "opclass": verdict["opclass"],
            "irreversible": irreversible,
            "touches_irreplaceable": touches_irreplaceable,
            "reason": verdict["reason"].strip(),
            "needs_confirmation": irreversible or touches_irreplaceable,
            "fail_safe": False,
        }
    except Exception as exc:
        return _fail_safe(exc)


def main(argv=None):
    """Minimal debugging CLI; normal investigator integration imports the function."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", required=True)
    parser.add_argument("--problem", required=True)
    parser.add_argument("--evidence-file")
    parser.add_argument("--capability", choices=("act", "experiment", "read"), default="act")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    evidence = []
    if args.evidence_file:
        try:
            with open(args.evidence_file, encoding="utf-8") as fh:
                loaded = json.load(fh)
            evidence = loaded if isinstance(loaded, list) else [str(loaded)]
        except Exception as exc:
            result = _fail_safe(f"could not read evidence file: {exc}")
            print(json.dumps(result, indent=2 if args.json else None))
            return 0

    result = classify_operation(
        args.question, args.problem, evidence, capability_ceiling=args.capability)
    print(json.dumps(result, indent=2 if args.json else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
