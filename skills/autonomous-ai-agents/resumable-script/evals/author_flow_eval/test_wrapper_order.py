"""Offline pin: state DECLARATION ORDER survives the wrapper byte-for-byte.

Under v2, sequential fall-through makes declaration order SEMANTIC — a sort_keys=True on the spec
serialization silently rewired authored graphs (caught live 2026-07-03: an alphabetized spec ran
begin->gate before triage, rendering an empty priority). This test needs no live environment.
"""
import json

import wrapper


def test_write_flow_preserves_state_order(tmp_path):
    spec = {"id": "order_pin", "version": 1, "start": "zeta",
            "states": {"zeta": {"run": "begin"}, "alpha": {"run": "record"}, "mid": {"run": "finish"}}}
    path = wrapper.write_flow(spec, str(tmp_path / "flow.py"))
    src = open(path).read()
    embedded = json.loads(src.split("SPEC = ", 1)[1].rsplit("\nflow =", 1)[0])
    assert list(embedded["states"]) == ["zeta", "alpha", "mid"]
