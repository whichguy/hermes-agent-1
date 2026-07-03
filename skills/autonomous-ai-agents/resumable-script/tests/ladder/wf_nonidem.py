"""wf_nonidem — a declarative non-idempotent run state (`"idempotent": false`). The fn contract
widens to (flowing, state, idem_key); the side effect is deduped on idem_key into $LEDGER, and
$CRASH=1 hard-kills the process mid-step -> a REAL dangling start -> exit 11 -> resume --resolve."""
import os

from workflow import load_workflow


def pay(flowing, state, idem_key):
    ledger = os.environ["LEDGER"]
    seen = set()
    if os.path.exists(ledger):
        seen = set(x for x in open(ledger).read().split("\n") if x)
    if idem_key not in seen:
        with open(ledger, "a") as f:
            f.write(idem_key + "\n")
    if os.environ.get("CRASH") == "1":
        os._exit(137)
    return {"paid": True, "idem": idem_key}


REG = {"pay": pay}

SPEC = {"id": "wf_nonidem", "version": 1, "start": "pay", "states": {
    "pay": {"run": "pay", "idempotent": False, "next": "@done"},
}}
flow = load_workflow(SPEC, REG)
