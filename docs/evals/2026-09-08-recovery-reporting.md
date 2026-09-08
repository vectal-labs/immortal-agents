# Recovery reporting verification

## Reproduced gaps

The real state-file / loopback-webhook suite failed before the watcher fix:

- A recovery observed after 11 minutes had already lost its pending observation.
- BB tool-only progress produced no success alert.
- A later unrelated BB request falsely confirmed the earlier resume.

The same scenarios now pass. Request acceptance and session identity determine
which work counts; elapsed time does not. Existing tests for expired observation
were updated to the new persistent-observation contract, retaining explicit
failure/replacement coverage. Synthetic BB continuations now include the actual
request ID and acceptance event rather than assuming a started turn is accepted.

## Coverage

- Ordinary terminal recovery, BB provider retry, rejected submission, daemon interruption.
- Claude text/tools, Pi tools, Codex calls, BB command/file/tool activity.
- Delayed progress, watcher restart, unavailable logs, partial JSONL writes.
- New human input, queued follow-ups, replaced sessions, rejected/failed/interrupted turns.
- Automatic Claude compaction summaries and Codex injected context.
- Native Codex retry records, dedicated operational events, multiple episodes in one turn.
- BB/native ID mapping and duplicate suppression in either observation order.
- Missing webhook throughout recovery, HTTP failures, rate limits, deleted/repaired webhooks.
- Atomic-save failures and replay through a new process.

The webhook tests use a real local HTTP server and real state files. They require
the message acknowledgement and verify that repeated scans do not send another
request. No test sends a message to the configured Discord webhook.

The native fixture is a trimmed actual Codex app-server recovery capture.
Its SQLite retry and matching rollout progress pass through the observer and
local webhook with one delivered alert. The original fixture's shell-tool
execution assertion failed inside the outer sandbox; this capture proves
transport/output recovery, not successful shell execution.

## Evidence and commands

- [Actual event audit](2026-09-08-recovery-reporting-events.md): 20 BB thread samples,
  24 CLI logs and 11 archived/regression fixtures.
- [Primary-source research](2026-09-08-recovery-reporting-research.md).
- Native formats were additionally checked against 495 actual retry log records.
- BB/native turn IDs differed in all 20 matched sessions; completion checkpoints
  and retained raw provider events supply the mapping.
- Final Python suite: **586 tests passed** in 89.6 seconds, including 40 native
  observer tests. Release tooling: 12 tests passed after the version/patch update.
- Native unit tests: 3 retry/reporting and 11 compaction tests passed, including
  a failed confirmation write followed by database recovery. The initial nextest
  filename filter selected zero tests; corrected module filters ran the compiled
  test binary and included both new reporting regressions.
- All 24 native log tests passed, including recovery-event retention across row,
  byte and age limits. Native unit-test total: **38 passed**.
- Final source-built CLI: **5/5 end-to-end cases passed** with `RUST_LOG=off`.
  Persistent silent-stream recovery, ephemeral silent-stream recovery and ephemeral
  peer closure each saved one success and delivered one acknowledged local webhook
  message across two watcher processes. The healthy control and cancelled recovery
  delivered none. Ephemeral cases saved no rollout; events contained only operational
  IDs. The same five baseline cases passed their transport checks but saved zero
  reporting events before the patch.
- Tested CLI SHA256: `92eabe6c87363e4019a00e4f75653023988464dff56c5819230ed7907f6044e2`.
  This is local source-build evidence, not a signed distribution or installation.
- All three pinned patches passed checksum verification and applied to upstream
  commit `3d2ee51ca2d5db578f328aa75e20aa22c0197c9a`. The five changed native source
  files match the prepared release source. Unchanged helper downloads were skipped
  for this source-application check.

```sh
python3 -m unittest discover -s tests
python3 docs/experiments/0021-codex-exec-recovery-reporting.py --codex /path/to/rebuilt/codex
```

Use Python 3.11 or newer for the test suite. The native command uses isolated
homes/workspaces and loopback peers. It does not change the Mac's networking.

## Deployment limits

This change does not retrospectively reconstruct recoveries whose source records
were never saved. The first native scan establishes a cutover; subsequent scans
resume persisted cursors. Older interactive Codex builds need retained retry logs
and rollout history. Exec/ephemeral coverage requires the new native patch.
Custom Codex homes must be configured for observation.

Pending Discord deliveries are retained, including while the webhook is missing
or blocked. A lost Discord acknowledgement can cause a duplicate; exactly-once
external delivery is not claimed. Lost/corrupted local storage remains outside
the persistence guarantee.
