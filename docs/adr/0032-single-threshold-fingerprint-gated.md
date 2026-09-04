# 0032 — One 120s threshold for all harnesses, gated by the death fingerprint

## Context

Codex self-recovers from outages under ~5 minutes (experiments 0003/0004);
Claude Code dies at ~70s. A per-harness threshold (Codex 300s) is viable, but
timing alone never triggers a revive anyway — the pane must show the harness's
death fingerprint (ADR 0008 three-signal rule).

## Decision

Keep the single MIN_OUTAGE_SECS = 120 for every harness and let the
fingerprint gate the action: no fingerprint, no revive (David, 2026-08-27).
A surviving Codex never shows the error, so the lower threshold cannot cause
a false positive.

Fallback: if evidence later shows this is insufficient for Codex (e.g. a
premature revive or noisy skips), switch to a per-harness threshold with
Codex at 300s — that path stays viable and is the first retry.

## Consequences

- One number to reason about; no per-harness timing config yet.
- Codex panes evaluated at 120s+ will usually skip with "no fingerprint" until
  the ~5 min mark — expected, logged, harmless.
