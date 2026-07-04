# NBQ experiment pre-registration

Fill every blank before building.

## Hypothesis

- Candidate / backlog ID: ___
- Hypothesis: ___
- Prior closed experiment affected, if any: ___
- Why its old verdict no longer applies, if re-opened: ___

## Expected mechanism

- Causal mechanism: ___
- Observable consequence if correct: ___
- Cheapest falsifying observation: ___

## Smoke test

- Command / procedure: ___
- Pass condition: ___
- Stop condition: ___

## Targeted tests

- Tests: ___
- Inert-by-default pin: ___
- Required assertions: ___

## Gate (arms, n, primary metric)

- Control arm: ___
- Experimental arm(s): ___
- Paired sample and n: ___
- Primary metric: ___
- Secondary diagnostics: ___

## Mechanical ADOPT rule

- Adopt exactly when: ___
- Otherwise: no-adopt / adopt with knob off by default (choose one): ___

## Efficiency budget

- Expected Δtokens per run: ___
- Expected Δwall per run: ___
- Expected Δcalls per run: ___
- Ceiling that vetoes even a result win: ___
- If the ceiling is exceeded: no-adopt / adopt with knob off by default (choose one): ___

## Rollback (selector + flag)

- Selector: ___
- Rollback flag / value: ___
- Absent-key behavior: ___

## Journal stubs for BOTH outcomes

### If adopted

> **[ID / candidate] — ADOPTED.** Hypothesis: ___. Gate: ___ at n=___. Δresult: ___. Δcost:
> Δtokens ___, Δwall ___, Δcalls ___. The pre-registered rule passed because ___. Default/selector
> decision: ___. Evidence: ___.

### If negative result

> **[ID / candidate] — NO ADOPT.** Hypothesis: ___. Gate: ___ at n=___. Δresult: ___. Δcost:
> Δtokens ___, Δwall ___, Δcalls ___. The pre-registered rule failed because ___. The feature stays
> off / is removed because ___. Re-open only if ___. Evidence: ___.
