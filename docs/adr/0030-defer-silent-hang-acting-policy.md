# 0030 — Defer the silent-hang acting policy until experiment 0003

## Context

Silent hang appears to be Codex's default death mode, but ADR 0019 makes the
watcher observe-only on hangs, so acting would break the zero-false-positive
rule on guesswork. Per ADR 0029, evidence comes first.

## Decision

Defer (David, 2026-08-23). Whether the watcher may act on suspected hangs —
and any "how long is stuck" threshold — is decided only from experiment 0003's
observed Codex v0.149 death behavior. Until then: observe and log only.

## Consequences

- The stuck-threshold question is moot until the acting question is settled.
- Experiment 0003 must capture: pane text over time, rollout JSONL tail, and
  whether any error record ever appears after a real cut.
