# DoD: demo-done   STATE: satisfied

INTENT: The CLI ships with resumable state across crashes.
HARD (inviolable): no state corruption on kill -9
SOFT (relaxable, ranked): 1) journal stays human-readable

REQUIREMENTS   (markers: ○ unmet · ✓ met (receipt) · ~ waived (receipted reason))
- R1   completed work survives a crash                       [after: —]
  - R1.1  a killed run resumes without re-executing completed steps   check: cmd — tests/test_replay.py green   ✓ replay suite 6/6 green, kill-resume demo receipt in journal
  - R1.2  the journal is append-only under concurrent runs   check: cmd — tests/test_lock.py green   ✓ lock suite green; flock receipt captured
- R2   operators can inspect state                           [after: R1]
  - R2.1  state.json summarizes progress at any time   check: judge — a reader can tell completed vs pending steps without the code   ✓ reviewed sample state.json; fields self-describing
  - R2.2  a web dashboard renders the journal   ~ waived: out of scope for v1, CLI inspection suffices
OPEN:
AMENDMENTS:
- c1 R2.2 waived — out of scope for v1, CLI inspection suffices
- c1 R1.2 added — concurrent-run corruption discovered during c0 execution
