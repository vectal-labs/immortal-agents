# 0029 — Experiments before architecture, always

## Context

Twice now, a real outage test overturned assumptions: silent-hang candidates
self-recovered (experiment 0002), and Codex's death mode differs from what
older bug reports suggested. Guessed architecture gets rewritten; observed
architecture sticks. ADR 0004 said experiment-first for the first MVP —
David is a big fan of this as a standing rule.

## Decision

For every new capability, harness, or failure mode in this project: run the
real experiment first, observe what actually happens, then make the
architectural decision from that evidence (David, 2026-08-23). Never design
detection, resume, or policy logic from documentation, bug reports, or
intuition alone.

## Consequences

- Decisions blocked on missing evidence get deferred with a named experiment,
  not debated (e.g. ADR 0028).
- Experiment write-ups in docs/experiments/ are the required input to
  architecture ADRs.
