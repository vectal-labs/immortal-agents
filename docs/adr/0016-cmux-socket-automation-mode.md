# 0016 — cmux socket control mode set to Automation permanently

## Context

cmux's local Unix socket defaults to `cmuxOnly`, which blocks external clients
from typing into panes. The watcher delivers resumes by sending ESC + "keep
going" + Enter into a cmux pane (ADR 0010), so it cannot work at all in the
default mode. Only David can change this setting (cmux Settings → Automation).

## Decision

David set Socket Control Mode to "Automation mode" permanently (2026-08-23).
Not per-test: the watcher needs it always-on in production, and the socket only
accepts local clients from the same macOS user.

## Consequences

- End-to-end testing and production resume delivery are unblocked.
- Any local process running as the operator's user can control cmux panes. Accepted:
  same trust boundary as the user's own shell.
