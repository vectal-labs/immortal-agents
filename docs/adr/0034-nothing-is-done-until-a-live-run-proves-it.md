# 0034 — Nothing is done until a live run proves it

## Context

ADR 0029 covers the front of the loop: experiment before designing. ADR 0022
defined "done" for the Claude MVP only. The Codex detect-and-resume build
(ADR 0031-0033) passed replays against real captures from experiment 0004 —
and still nobody had watched the watcher revive a Codex session by itself.
Replays prove the logic; only a live run proves the wiring (pane reading,
timing, keystroke delivery, launchd).

## Decision

Nothing is done until a real outage on this Mac shows the watcher doing the
right thing unattended, proven by logs alone (David, 2026-08-27). This applies
to every change in detection or revive behavior, for every harness. Until that
run happens, the change ships as "built, unverified" and is labeled that way in
docs and commit messages.

## Consequences

- Every behavior change gets its own experiment number and write-up in
  docs/experiments/.
- "It works in the replay" is never the last sentence about a change.
- Live runs still follow ADR 0015/0020: David's fresh approval, his timing.
