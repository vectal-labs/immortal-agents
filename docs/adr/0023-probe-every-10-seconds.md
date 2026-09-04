# 0023 — Probe connectivity every 10 seconds

## Context

The watcher probed captive.apple.com every 20 seconds. With a 120s resume
threshold (ADR 0017), that gives only ~6 data points per outage window —
coarse timing data for experiments like the upcoming outage test.

## Decision

PROBE_SECS = 10 (David, 2026-08-23). Twice the data per outage, tighter
outage-start timestamps.

## Consequences

- Outage detection latency and timing precision improve to ~10s.
- One tiny HTTP request every 10s — negligible load, still quiet (ADR 0012).
