# 0024 — Map a pane to its session via cmux hook-sessions

## Context

The real outage test exposed a flaw: `find_jsonl(cwd)` picks the newest JSONL
in the pane's cwd project folder, so sessions sharing a cwd all matched the
same file. Skips were saved only by the pane-content signal — two mid-task
sessions in one folder could cross-contaminate the JSONL signal.

## Decision

Map each cmux surface to its exact session file via cmux's own bookkeeping
(David, 2026-08-23): `~/.cmuxterm/claude-hook-sessions.json` →
`activeSessionsBySurface[surface_id].sessionId` → 
`~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl`. No newest-file guessing.

## Consequences

- Each pane is judged only on its own transcript; same-cwd sessions can no
  longer cross-contaminate.
- New dependency on cmux's hook-sessions file format; a format change breaks
  mapping (fallback behavior: ADR 0025).
