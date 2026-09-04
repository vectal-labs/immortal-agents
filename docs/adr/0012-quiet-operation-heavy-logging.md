# 0012 — Quiet operation, heavy logging and observability

## Context

When internet returns and the watcher resumes sessions (or decides not to), it could notify the user, ask for confirmation, or stay silent. Notifications add noise; asking defeats autonomous recovery.

## Decision

The watcher is quiet — the user should not even notice it running. No notifications, no prompts. In exchange, it must have LOTS of logging, traces, and observability: every probe state change, every session evaluated, every signal checked, every decision (resume or skip) and why, every keystroke sent. If anything goes wrong, the logs alone must be enough to debug it.

## Consequences

- All behavior is reconstructable from the log file after the fact.
- Log verbosity is a feature, not a bug — err on logging too much.
- Needs simple log rotation or size capping so it can run forever.
