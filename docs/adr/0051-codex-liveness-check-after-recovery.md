# 0051 — Codex death = idle with an unfinished turn after recovery, not a version check

## Context

ADR 0033 defines a dead Codex by three signals, the third being a
`task_complete` written inside the outage. That matched Codex 0.149 through
a local proxy (experiment 0004: dies at ~5 min, error at the idle prompt).
Codex 0.153.2 direct-to-OpenAI never does this. Experiment 0016 (21 min)
and 0017 (92 min) both show it retrying forever with
`Reconnecting... waiting for network`, then finishing the turn by itself.
The third signal is now unreachable. The watcher's `skip` was correct, but
the rule only recognises one specific way of dying, and Codex could bring
back a retry limit in any release.

## Decision

Do not gate on the Codex version or config (David, 2026-09-04). Detect the
outcome instead. After recovery, a Codex turn that started before the cut
and has no `task_complete` yet gets a liveness check after a grace period
(2–3 min): if the rollout is still being written, or the pane shows
`Working` or `Reconnecting`, Codex is alive and is skipped; if the pane is
idle at the prompt and the rollout has been silent since the outage, Codex
is dead and gets `keep going`. The ADR 0033 signal stays as a fast path.
`Reconnecting... waiting for network` is never a death fingerprint.

## Consequences

- Works for today's self-healing Codex (skip), for 0004-style deaths
  (resume), and for any future retry limit, with zero version logic.
- The Codex decision can arrive a few minutes after recovery instead of
  immediately.
- The same outcome-based pattern is the template for Claude Code and Pi
  if their behaviour changes.
- ADR 0033 stays as history; its third signal is no longer required.
