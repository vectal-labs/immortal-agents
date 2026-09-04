# 0017 — Resume threshold: 120 seconds

## Context

Claude Code retries on its own for roughly 70 seconds after connectivity drops
before giving up (experiment 0001). Acting before that window ends risks
resuming a session that would have recovered by itself. Candidates were 60s
(original), 120s, 180s, and 300s.

## Decision

MIN_OUTAGE_SECS = 120. An outage must last at least 120 seconds before the
watcher considers resuming anything (David, 2026-08-23, over the CTO's 180s
recommendation — faster recovery mattered more).

## Consequences

- Dead sessions get revived ~1 minute sooner than with 180s.
- 120s sits above the observed ~70s give-up point but inside Claude's worst-case
  retry window. Mitigation: timing alone never triggers a resume — the JSONL
  `isApiErrorMessage` fingerprint and pane content must also confirm the session
  actually died (ADR 0008).
- Test outage windows must exceed 120s; use ~3 minutes to be safe.
