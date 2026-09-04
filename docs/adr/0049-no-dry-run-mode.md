# 0049 — No dry-run mode

Status: accepted (David, 2026-09-03)

## Context

A dry-run mode was proposed for the open-source release: log every decision
but never type "keep going". The idea was to let strangers watch the watcher
before trusting it.

## Decision

No dry-run mode, ever. It is a total waste of time and a distraction. This is
a simple project with low risk: the only action is typing "keep going" into a
session that already died, and the log already records every decision.

## Consequences

- The dry-run worker thread was archived and its changes discarded.
- The revive path stays as is. Trust comes from the live experiments in
  `docs/experiments/`, not from a rehearsal mode.
