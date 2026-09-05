# Incident 0002 — bb submission timeout was silently skipped

September 5, 2026. Times below are UTC.

## Summary

bb failed to restore the Codex session in `remove_tokenmaxx`. The user's next
instruction never started. The watcher found the failed thread but ignored the
command error, so it neither retried the request nor alerted the user.

## Timeline

- 10:50:50.196: bb recorded request `creq_x49j9b62ab`.
- 10:51:20.222: bb recorded `JSON-RPC request timed out: thread/resume`.
- 10:51:25.999 onward: watcher evaluations contained null error details and timestamps.
- 11:09:57.906: a disposable bb thread reproduced a real `turn/start` timeout.
- 11:12:35.640: the new recovery path submitted the disposable thread's original request.
- 11:12:45.700: new assistant output confirmed recovery.

Sources: trimmed [failure fixture](../../tests/fixtures/bb_submission_timeout.json),
watcher log, bb thread logs, and [experiment 0018](../experiments/0018-bb-submission-recovery.md).

## Root causes

- [The bb adapter](../../immortal/hosts/bb.py) only read provider errors, while this failure used `client/turn/rejected` and `system/error`.
- [The provider scan](../../immortal/detect/bb_provider.py) excluded network-classified timeouts. Nearby probe failures lasted 26–32 seconds, below the separate 120-second outage threshold.
- [The revive loop](../../revive.py) treated two missing error timestamps as an already-seen error and silently skipped the target.

## What worked

The watcher remained running and could read bb. bb retained the rejected input
and provided a guarded retry command. Releasing an errored runtime preserved
that request and its history.

## Fixes

- Parse rejected submissions with their current and original request IDs.
- Retry known startup/resume timeouts through the online scan, with 30/60/120-second delays and three attempts.
- Persist reservations before dispatch. Do not resend an unchanged failure after an ambiguous reply or cancelled queued retry.
- Recheck current work before every action; release the failed runtime before the second attempt.
- Log missing error details and alert once. Confirm output from the retried turn before calling recovery successful.

Implementation: [bb recovery policy](../../immortal/core/bb_recovery.py),
[behavior tests](../../tests/test_bb_submission.py), and
[measurement notes](../evals/2026-09-05-bb-submission-recovery.md).

## Lessons

Failure before an agent starts is a separate recoverable state. Preserve the
original request, and treat a timed-out submission's delivery as uncertain until
bb's request history resolves it.
