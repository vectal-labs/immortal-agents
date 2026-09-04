# Recovery outcomes

`revive_attempt.result` records prompt delivery. It does not mean the agent resumed work.

After delivery, the watcher checks for new assistant output in the same session
every 30 seconds while online, for up to 10 minutes:

- `revive_confirmed`: new assistant output was observed after the prompt.
- `revive_unconfirmed`: no output was observed, the session could not be read,
  or another retry replaced the attempt. This does not prove recovery failed.

New assistant text or tool calls count as output. User input, old output,
tool results alone, and recognized error messages do not confirm recovery.
Pending checks survive watcher restarts. Checks never send another prompt.
Confirmation proves observed activity, not task completion or causation.

Both events include an attempt ID, host, harness, reason, and elapsed seconds.
They are logged locally and sent remotely only when telemetry is enabled.
Session paths, thread IDs, prompts, and output text stay local.

The telemetry server accepts at most 16 KiB per request and event, with a
nonblank event name of at most 128 characters. Stalled reads time out after
5 seconds. Logs rotate at 10 MiB with one backup (20 MiB total). `/stats`
counts retained events, skips corrupt records, and excludes rotated-out history.

Codex skips folders with multiple recent matching sessions (`ambiguous_session`).
It needs an unambiguous session before sending input.
