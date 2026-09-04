# 0050 — Outage window starts at the last good probe

## Context

The watcher probes every 10s. Until now the outage window opened at the
first failed probe. But the internet was already gone somewhere after the
previous good probe, and that gap is not always 10s: a closed lid or a slow
probe can stretch it to minutes. Experiment 0014 (2026-09-03): the Mac slept
4 minutes, Cursor died 1.3s before the first failed probe, and the watcher
skipped it as `timing_miss`. ADR 0045 already moved the window *end* from the
probe to "APIs resolve"; the *start* had the same kind of gap.

## Decision

The outage window opens at the last probe that passed (David, 2026-09-04).
The first failed probe is logged as `detected_at`, not as the loss time. A
fresh watcher with no good probe yet uses the detection time.

## Consequences

- Deaths in the detection gap (sleep, lid close, hung probe) are inside the
  window and get revived.
- The window is up to one probe interval wider, or a whole sleep wider. An
  agent that died for another reason just before the cut could receive a
  `keep going` it did not need. Accepted; the three-signal rule still applies.
- Outage duration is measured from the last good probe, so it grows by the
  same gap.
