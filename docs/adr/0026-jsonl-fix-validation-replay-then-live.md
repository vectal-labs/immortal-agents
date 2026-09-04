# 0026 — Validate the JSONL fix by replay now, live cut later

## Context

The pane→session mapping fix (ADR 0024/0025) needs proof. A live outage test
requires David to launch a cut (ADR 0020); the silent-hang experiment
(ADR 0019) already needs one.

## Decision

Both, staged (David, 2026-08-23): (1) now — offline replay with a synthetic
two-sessions-one-folder fixture plus the existing 0001 captures, asserting each
pane matches only its own transcript; (2) later — one live outage test with two
mid-task sessions sharing a cwd, bundled into the same David-launched cut as
the silent-hang experiment.

## Consequences

- Logic is proven before any new cut; the live cut count stays at one.
- The bundled test window must satisfy ADR 0022-style log-only proof for both
  goals in a single run.
