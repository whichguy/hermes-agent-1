from engine import flow


@flow(id="lglue")
def main(ctx, inp):
    # A throw BETWEEN steps (glue) — the failed payload must keep the bare {name,message}
    # shape (no step/attempts provenance, which is step-failure-only).
    ctx.step("ok", lambda: 1)
    raise ValueError("glue broke")
