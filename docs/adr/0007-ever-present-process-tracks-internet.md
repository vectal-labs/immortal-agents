# 0007 — An ever-present process tracks internet connectivity itself

## Context

To know that a session stopped *because of* internet loss, we need to know exactly when the internet dropped and when it came back. No Claude Code session can tell us that reliably.

## Decision

This program runs as an ever-present background process. It monitors internet connectivity continuously, independently of any Claude Code sessions, and records the timestamps of every loss and recovery.

## Consequences

- We always have the exact internet-drop and internet-recovery timestamps to correlate against session activity.
- Detection can use a tight time window instead of a blunt staleness threshold.
- The process must be reliable enough to run permanently (survive reboots, not leak resources).
