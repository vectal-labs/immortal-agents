# 0019 — Silent hangs are core product scope

## Context

Some sessions may die from an outage without writing the `isApiErrorMessage`
fingerprint — they just freeze. The CTO proposed shipping the MVP without
handling them. David rejected that (2026-08-23): reliably spotting *every*
outage-killed session is the core product offering, not an edge case.

## Decision

Silent-hang detection is in scope. The path is experiment-driven, same as
experiment 0001: stay Claude Code-only on a single harness, run controlled
outage experiments, observe how sessions actually fail, and derive a reliable
fingerprint for hung sessions. No guessed heuristics ship — detection signals
must come from observed behavior, and the no-false-positives rule (README)
still binds.

## Consequences

- The MVP validation plan gains an experiment: induce or capture a silent hang
  and document its JSONL/pane signature (docs/experiments/).
- Until a reliable signature exists, the watcher logs suspected hangs
  (outage overlapped, no error record, no activity) without acting on them.
- Being Claude Code-only (ADR 0001) is reaffirmed as the strategy that makes
  this characterization tractable.
