# 0015 — No internet-cut tests without explicit approval

## Context

Experiment 0001 cut all internet routes on this Mac while the operator was actively working. It disrupted them for 10–15 minutes. Connectivity-killing tests are inherently machine-wide: they hit every app and person using the computer, not just the test harness.

## Decision

Never run any test that cuts or degrades internet connectivity on this Mac without David's explicit approval, obtained fresh for each test session. Blanket or standing permission does not count. Before an approved cut: confirm the operator is not actively working, keep the outage window as short as the test allows, and announce when it starts and ends.

## Consequences

- Every validation run involving a real outage starts with asking David and waiting for a yes.
- Tests must be prepared so the approved window is used efficiently — one clean cut, not repeated trial-and-error cuts.
- If this rule is ever broken, that is treated as a serious process failure, not a minor slip.
