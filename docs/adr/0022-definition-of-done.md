# 0022 — Definition of done for the MVP

## Context

Validation was never completed end-to-end: run 1 died of a shell signal, run 2's
outage leaked through a secondary network route. A crisp finish line was needed.

## Decision

The MVP is done when one real all-route outage test — launched by David himself
(ADR 0020) — with three live Claude Code cmux sessions proves, by watcher logs
alone: (1) the session dead mid-task was resumed, (2) the finished session was
skipped, (3) the session waiting for user input was skipped, both skips with
logged reasons (David, 2026-08-23).

## Consequences

- One ~3-minute test window David controls settles both product value and the
  no-false-positives rule.
- Silent-hang characterization (ADR 0019) is the immediate next experiment
  after done, not a blocker for it.
