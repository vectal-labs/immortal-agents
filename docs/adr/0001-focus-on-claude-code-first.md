# 0001 — Focus on Claude Code only, first

## Context

The problem is AI coding agents that stop running when the computer loses internet. Many different agents exist (Claude Code, Codex, Cursor CLI, etc.). Supporting all of them at once would slow us down.

## Decision

Start very minimal. Focus on Claude Code only, as a simple script. Other agents can come later.

## Consequences

- Detection and resume logic can rely on Claude Code specifics (e.g. session files in `~/.claude/projects/`).
- Adding other agents later will need per-agent detection and resume adapters.
