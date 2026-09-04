# 0020 — David launches test outages himself

## Context

Decision on the leak-proof test method: a script cuts all routes (Wi-Fi, USB
ethernet, Tailscale), verifies the probe is dead for the full window (~3 min,
above the 120s threshold), then auto-restores. During a previous test David
did not know a cut was happening, was doing real work, and rescued the
connection mid-test — ruining the run and his focus. ADR 0015 already requires
fresh approval; approval alone still leaves timing in the agent's hands.

## Decision

Agents build and maintain the outage script, but never execute it. Only David
launches a test outage, himself, at a moment he chooses (David, 2026-08-23).
The deliverable is a single command he can run when ready.

## Consequences

- No surprise cuts, ever. David is in full control of timing.
- The script must be self-contained: verify all routes are dead, hold the
  window, auto-restore, and log timestamps — no agent involvement mid-run.
- Agents wait for David's "test done" (or read the logs afterward) instead of
  scheduling or arming cuts, superseding the "detached script armed by the
  agent" pattern from earlier plans.
