from engine import flow


@flow(id="g4")
def main(ctx, inp):
    # Intervention where the user supplies a corrected RECORD (structured answer), not yes/no.
    fix = ctx.ask("provide-fix", {"prompt": "Provide the corrected record (JSON).", "type": "object"})
    applied = ctx.step("apply", lambda: True)
    return {"fix": fix, "applied": applied}
