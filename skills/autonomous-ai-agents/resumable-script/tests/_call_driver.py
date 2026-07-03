#!/usr/bin/env python3
"""Test utility (NOT a flow fixture) for run_call_ladder.py: drives run_flow/resume_flow inside a
SEPARATE, expendable process. Used to (a) prove a portable state blob is usable from a genuinely
different process, not just held in memory, and (b) let a fixture's os._exit() simulate a crash
without killing the actual test runner. Reads one JSON request on stdin, prints {"code","payload"}
JSON on stdout — or the process just dies, which the caller observes via returncode.

Request shape: {"module": "<ladder fixture module name>",
                "state"?: <portable-state blob -> resume_flow>, "input"?: <-> run_flow>,
                "answer"?: ..., "resolve"?: ..., "resolve_key"?: ..., "resolve_value"?: ...,
                "env"?: {"K": "V", ...}}
"""
import importlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(HERE, "ladder"))
import engine  # noqa: E402

req = json.load(sys.stdin)
os.environ.update(req.get("env", {}))
mod = importlib.import_module(req["module"])
if "state" in req:
    payload, code = engine.resume_flow(mod.main, req["state"], answer=req.get("answer"),
                                       resolve=req.get("resolve"), resolve_key=req.get("resolve_key"),
                                       resolve_value=req.get("resolve_value"))
else:
    payload, code = engine.run_flow(mod.main, req.get("input"))
print(json.dumps({"code": code, "payload": payload}))
