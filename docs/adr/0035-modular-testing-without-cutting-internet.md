# 0035 — Modular design: test harness revives without cutting the Mac's internet

## Context

Every meaningful change so far ended in a full machine-wide internet cut
(experiments 0001-0004). Each one makes the operator's MacBook unusable for
10-15 minutes — a real productivity killer, and it makes "prove it live"
(ADR 0034) expensive enough that it gets postponed. The trigger (probe loop +
outage timing) is already proven; re-testing it on every change is waste.

## Decision

Split the program into independently testable modules with one boundary rule:
the trigger is frozen and never re-tested; everything downstream is tested
with a *simulated* outage while the Mac stays online (David, 2026-08-29).

- **trigger** — probe loop, outage state, recovery event. Done. Not re-tested.
- **harness/** — one module per agent (claude, codex): pane markers, death
  fingerprints, session-log rules. Unit-tested against captured fixtures.
- **revive** — cmux key delivery (ESC, "keep going", Enter). One shared path.
- **sim** — makes only the agent harnesses believe the internet is gone: a
  local pass-through proxy the test panes point at (Codex via
  `openai_base_url`, Claude via `ANTHROPIC_BASE_URL`) that can be flipped
  dead for N minutes, plus a simulated-outage flag the trigger honors so the
  live watcher runs the exact real recovery path.

Full machine-wide cuts become rare release gates, not the per-change test.
ADR 0034's "live run" is satisfied by a simulated outage against a real
running session.

## Consequences

- The operator keeps working while harness revives are tested.
- Replays stop being throwaway scripts and become the test suite.
- The proxy must reproduce each harness's real death (Codex: 502 Provider
  unreachable — it already speaks to a local proxy; Claude: connection
  errors) — verified once against the 0001/0004 captures.
