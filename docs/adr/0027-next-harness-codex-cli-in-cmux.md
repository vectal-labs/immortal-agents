# 0027 — Next harness: Codex CLI in cmux

## Context

The MVP is proven for Claude Code in cmux: two real outage tests, resumes and
skips all correct (experiments 0001-0002, ADR 0022/0026). ADR 0001 said
Claude Code first — first implies a second.

## Decision

The next supported harness is Codex CLI, still inside cmux (David, 2026-08-23).
Same approach as before: run a real outage experiment first (ADR 0004) to learn
how Codex dies — its session-log location and death fingerprint, its pane
signature, and its revive keystrokes — before writing any detection code.

## Consequences

- Detection must become per-harness: Codex has its own session files
  (`~/.codex/`), its own error shapes, and cmux tracks it via
  `codex-hook-sessions.json`.
- Silent-hang hunting (ADR 0019) continues in parallel; it is not displaced.
- Scope guards still hold: cmux only, prolonged outages only, quiet operation.
