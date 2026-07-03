# DoD: demo-migration   STATE: agreed

INTENT: Every tenant's data lives in the new schema with consumers unaffected.
HARD (inviolable): no data loss; no fabricated rows
SOFT (relaxable, ranked): 1) zero downtime  2) finish within one maintenance window

REQUIREMENTS   (markers: ○ unmet · ✓ met (receipt) · ~ waived (receipted reason))
- R1   the new schema holds all data correctly              [after: —]
  - R1.1  every row in users has non-null tenant_id   check: cmd — psql -c "select count(*) from users where tenant_id is null" returns 0   ○
  - R1.2  row counts match the old schema per table   check: cmd — scripts/count_diff.sh reports zero drift   ○
- R2   consumers read the new schema without breakage       [after: R1]
  - R2.1  the API answers GET /health with 200 against the new schema   check: cmd — curl -sf $URL/health   ○
  - R2.2  the nightly report renders with identical totals   check: judge — report totals for the last closed month equal the pre-migration run   ○
OPEN: whether the analytics replica must migrate in the same window
AMENDMENTS:
