# 0039 — Incidents get a write-up in docs/incidents/

## Context

Experiments have had numbered write-ups since ADR 0004/0029 and they are what
made this project's decisions stick. Incident 0001 (agent left the Mac offline
for 25 minutes) got the same treatment ad hoc. It should be the rule.

## Decision

Anything that harms David's machine, his work, or his time — not just a
failed test — gets a numbered write-up in `docs/incidents/` before the fixes
are considered done (David, 2026-09-02). Required sections:

- **Summary** — one paragraph, plain English
- **Timeline** — from logs, with timestamps and file references
- **Root causes** — every one, with the primary source that confirms it
  (man page, code, log line)
- **What worked** — so the fix does not break it
- **Fixes** — done vs. required, and where each landed
- **Lessons** — the rule that would have prevented it

## Consequences

- Fixes reference the incident number in commits, ADRs, and AGENTS.md.
- Same rigor as `docs/experiments/`: evidence over recollection.
- An agent that caused the incident writes the first draft; David edits.
