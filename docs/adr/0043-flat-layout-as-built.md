# 0043 — Flat layout, as built (updates ADR 0035)

## Context

ADR 0035 planned a `harness/` package and a shared `revive` module. Neither
was built: ADR 0036 landed bb flat and left the split "a separate task", and
the README kept describing the planned layout as if it existed.

## Decision

Keep the flat layout (David, 2026-09-03):

- `watcher.py` — the trigger. Proven, frozen, never re-tested.
- `detect.py`, `detect_codex.py`, `detect_pi.py` — one module per harness.
- `host_cmux.py`, `host_terminal.py`, `host_ghostty.py`, `host_bb.py` — one
  module per host, each revives its own targets.
- `sim/` — simulated outage so downstream code is tested while the Mac stays
  online.

The 0035 testing rule stands. Only its layout is replaced. Docs describe files
that exist, not plans.

## Consequences

- No `harness/` or `revive` package unless a real need appears.
- README points here instead of restating the layout.
