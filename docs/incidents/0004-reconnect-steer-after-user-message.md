# Incident 0004 — Reconnect steer interrupted fresh user input

Date: 2026-09-14.

## Summary

A user restarted a Pi thread after waking the Mac. Immortal sent `keep going`
22 seconds later, while the agent was already running a command.

## Timeline

UTC, from the affected thread's `bb thread log --all --json` and
`~/.immortal-agents/watcher.log`:

- 16:37:44.653 — `client/turn/requested`: user submits fresh input.
- 16:37:44.686 — `turn/input/accepted`: the new turn accepts it.
- 16:37:50.964 — watcher detects reconnection.
- 16:37:51.905 — steering snapshot includes the now-active thread.
- 16:37:57.359 — agent starts a command.
- 16:38:06.521 — Immortal requests `keep going` on that same turn.

## Root causes

`core/steering.py` selected threads active after reconnect, including newly
started work. `hosts/bb.py::steer` checked status, approvals, and previous
nudges, but never checked recent user-input timestamps.

## What worked

The thread was genuinely active. Existing readiness, approval, pending-nudge,
and cooldown guards worked; this was a missing user-input guard.

## Fixes

- The steering pass supplies a cutoff 60 seconds before the reconnect tick.
  Slow scans, provider readiness, and wake/reconnect merging cannot move it.
- The BB adapter reuses a fresh history read immediately before dispatch.
  User requests since that cutoff cancel the episode's nudge via the existing
  persisted `superseded` result. System and other-agent messages do not count.
- Manual `keep going` and image-only requests count. Unreadable history or
  invalid user timestamps prevent sending. Normal failed-thread recovery is unchanged.
- `tests/test_reconnect_steer.py` replays the incident and covers delayed reads,
  restart persistence, cutoff boundaries, message authors, and unaffected threads.
  The new regressions failed before the fix and pass afterward.
- All 652 tests pass. An independent review found no material issues. A live
  read-only BB preflight check returned `superseded`; dispatch was forbidden.
  No connectivity was cut and no test messages were sent into existing threads.

## Lessons

Fresh user input supersedes automatic reconnect nudges; cancel rather than defer.
This remains a preflight guard, not an atomic BB send: input arriving after the
final history read can still race dispatch.
