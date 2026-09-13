# Incident 0003 — BB host connection loss was not recognized

Date: 2026-09-13. Thread: `thr_nftjj5drhr` (`design-finish`).

BB reported `host-daemon-restarted` with `cause=host-connection-lost`.
Immortal required the display sentence for a daemon restart, so it recorded
`no_provider_error` and made no recovery attempt. The watcher was running.

The same turn later completed. BB retained its error status because its
lifecycle rejected completion from that state. Completed work must not be
retried to repair a display error.

## Changes

- Immortal recognizes the structured interruption. It still requires matching
  accepted input, turn, error code, and interruption timing. Unknown causes,
  manual stops, newer work, and completed turns do not qualify.
- Existing retry reservations, limits, and request guards remain in use.
  Blocked retries now log the host, queue, interaction, or changed-failure reason.
- The separate BB source fix reconciles completion only for the latest matching
  interrupted turn. It preserves history, cancels that turn's obsolete queued
  retries, and rechecks retry eligibility inside the dispatch transaction.

## Verification

- `tests/fixtures/bb_host_connection_lost.json` contains the trimmed real events,
  including the later completion, without the user's prompt.
- All 638 Immortal tests passed.
- BB: 469 internal/thread tests and 27 lifecycle tests passed. Server/domain
  typechecks and the server build passed.
- An isolated BB HTTP server and the installed CLI exercised Immortal's recovery
  pass: one continuation retry, active thread, two total request records, and
  no duplicate after reloading watcher state. The eligible scan took 2.36 seconds.
  Host/provider execution was simulated; the test did not cut connectivity or
  send work into an existing thread.
- The 30-second minimum delay and polling schedule were not reduced.

## Activation

Commit the local changes, run `./install.sh restart`, and verify the running
source fingerprint. The BB source fix requires a BB application update;
restarting Immortal alone does not load BB server changes.
