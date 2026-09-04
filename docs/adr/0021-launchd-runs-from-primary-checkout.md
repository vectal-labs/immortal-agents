# 0021 — launchd runs the watcher from the primary checkout

## Context

The plist pointed at a temporary bb worktree path
(`~/.bb/worktrees/<env>/...`), which breaks as soon as the
worktree is cleaned. Alternatives: a separate deploy copy (safer against
in-repo edits, but adds a sync step) or the primary checkout.

## Decision

launchd runs `watcher.py` directly from `~/code/immortal-agents`
(David, 2026-08-23). One place, no deploy step, consistent with building
directly on main (ADR 0014).

## Consequences

- Edits to the repo change the live daemon on its next restart. Accepted:
  KeepAlive restarts it on crash and ~/.immortal-agents logs tell us.
- State and logs stay outside the repo in `~/.immortal-agents/`.
- `.gitignore` now excludes `__pycache__/`; the committed `.pyc` was removed.
