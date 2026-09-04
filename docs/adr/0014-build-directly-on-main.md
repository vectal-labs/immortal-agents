# 0014 — Build everything directly on main

## Context

This is a small, single-owner project: a few docs and one script. Working on side branches kept the MVP invisible on an unmerged branch while `main` looked docs-only, and merges added overhead without adding safety.

## Decision

All work happens directly on `main`, right away. Commit early, push often. No feature branches, no long-lived worktrees for this repo.

## Consequences

- `main` is always the current truth; nothing hides in a side branch.
- Fixing a bad commit forward is cheaper than branch ceremony at this size.
- Existing branches (`fix/remove-self-daemonize` etc.) stay as history; no new ones.
