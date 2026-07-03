"""call_leaf_gate — the simplest possible ctx.call CHILD: one durable human gate. Used as a leaf
by call_top_2level / call_mid_wraps_leaf. Import this module (never `from ... import main as x` —
that would bind a second flow-marked name into the importer's namespace and confuse load_flow's
first-match scan) and reference `call_leaf_gate.main` inline."""
from engine import flow


@flow(id="leaf")
def main(ctx, inp):
    ans = ctx.ask("gate", {"prompt": "leaf gate?", "options": ["ok"]})
    return {"leaf_ans": ans}
