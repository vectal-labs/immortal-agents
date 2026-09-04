# 0033 — Codex death = pane fingerprint + outage timing + task_complete inside the outage

## Context

Experiment 0004: a dead Codex writes `event_msg/task_complete` to its rollout
— indistinguishable from a clean finish — while the pane shows the error
(`unexpected status 502 … Provider unreachable … Unable to connect`, proxy
setup; upstream setups use the "stream error" family) and returns to the
idle prompt. The untouched control never self-recovered.

## Decision

Three signals, all required (David, 2026-08-27): (1) outage ≥ threshold
(ADR 0032); (2) pane text shows a Codex death fingerprint; (3) the session's
`task_complete` timestamp falls inside the outage window — a genuine finish
during a blackout is impossible, so this doubles as the stale-error guard
(cf. ADR 0018). Fingerprints cover both the proxy 502 family and the upstream
"stream error"/"Network error while contacting OpenAI" family.

## Consequences

- No surface→rollout mapping needed: any rollout in the pane's cwd whose
  `task_complete` lands inside the outage is dead by definition.
- Harness identification comes from the pane itself (Codex footer/prompt
  text), fixing the `unknown_harness` skip seen in experiments 0003/0004.
