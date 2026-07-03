# DoD: demo-smelly   STATE: draft

INTENT: The flaky auth test is deterministic and green in CI.
HARD (inviolable): no test deleted or skipped
SOFT (relaxable, ranked): 1) keep runtime under 5 minutes

REQUIREMENTS   (markers: ○ unmet · ✓ met (receipt) · ~ waived (receipted reason))
- R1   the root cause is understood                          [after: —]
  - R1.1  run the test 50 times and look at the failures   ○
  - R1.2  the failure has a written root-cause note   ✓
- R2   the fix holds                                         [after: R1, R9]
  - R2.1  the test passes 50 consecutive CI runs
  - R2.2  no other test got slower   ~
- R2   duplicate group id on purpose                         [after: R1]
OPEN:
AMENDMENTS:
