# 0008 — Detection = timing + session JSONL tail + terminal screen check

## Context

We must decide that a session stopped specifically because of internet loss, with near-zero false positives (ADR 0003). Candidate signals: internet-drop timing, the session JSONL tail, and the visible terminal content of the cmux pane.

## Decision

Use all three, for reliability in the MVP:

1. **Timing** — the session was active right up to the internet-drop timestamp (from ADR 0007), then went silent.
2. **JSONL tail** — the last records in `~/.claude/projects/.../<session>.jsonl` look mid-task (e.g. `tool_use`), not a completed turn or waiting-for-input.
3. **Terminal screen** — the cmux pane content shows the actual network error or "offline" indicator.

All three must agree before a session is marked as "stopped by internet loss".

## Consequences

- Strong guard against the worst failure: resuming a session that was waiting for user review.
- Requires cmux (ADR 0005) to read pane content.
- More signals to test in the experiment (ADR 0004).
