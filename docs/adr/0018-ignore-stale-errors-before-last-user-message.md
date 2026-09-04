# 0018 — Ignore API errors older than the last user message

## Context

The detector reads the session JSONL tail for the `isApiErrorMessage`
fingerprint (ADR 0008). An error from a previous outage can linger in the log
after David already revived the session manually. Counting it would make the
watcher type into a healthy session — a forbidden false positive.

## Decision

Only an API-error record with a timestamp *after* the last user message in the
JSONL counts as a death signal (David, 2026-08-23). Errors at or before the
last user message are stale and are skipped, with the skip reason logged.

## Consequences

- Manual revives ("keep going" typed by David or the watcher) reset the clock:
  the resume itself is a user message, so the old error stops matching.
- Costs one extra timestamp comparison in detect.py; no new state to persist.
