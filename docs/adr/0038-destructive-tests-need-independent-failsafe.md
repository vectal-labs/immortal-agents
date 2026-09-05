# 0038 — Every destructive test needs a fail-safe that does not depend on the test

## Context

Incident 0001: the outage script's own restore was the only way back. When
the script was killed mid-cut, nothing else turned the network on, and the
agent that launched it was offline too. ADR 0037 fixes this for internet cuts
specifically (netguard + restore timer). The pattern is general.

## Decision

Any test that turns something off on the operator's Mac — network, a daemon, a
device, a setting — must have a recovery that runs even if the test process,
the agent, and the terminal all die (David, 2026-09-02). Two requirements:

1. **Independent.** The recovery lives in its own always-on process (a
   LaunchAgent like netguard) or a pre-armed one-shot timer, started and
   verified *before* the destructive step.
2. **Idempotent and one-directional.** It only turns things back on. It never
   turns anything off, so it can run forever without harm.

No fail-safe, no test.

## Consequences

- netguard is the template: `StartInterval` LaunchAgent, reads a marker with
  a deadline, restores, never disables.
- Test scripts write the marker before acting and delete it on clean restore.
- "The script restores itself on exit" is not a fail-safe.
