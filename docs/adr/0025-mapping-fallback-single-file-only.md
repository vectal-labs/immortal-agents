# 0025 — Mapping fallback: single-file folders only

## Context

ADR 0024 maps panes to sessions via cmux hook-sessions. The mapping can be
missing: stale hooks file, a session not launched through cmux's wrapper, or a
format change. Falling back to "newest JSONL in cwd" reopens the
cross-contamination flaw; always skipping loses coverage.

## Decision

When no surface→session mapping exists (David, 2026-08-23): if the pane's cwd
project folder holds exactly one JSONL, use it — one file cannot
cross-contaminate. Otherwise skip the pane and log `no_session_mapping` with
the candidate count. Never guess among multiple files.

## Consequences

- Solo sessions stay covered even without cmux bookkeeping.
- Ambiguous panes are false negatives, never false positives — consistent with
  the README's one forbidden failure.
