# Recovery outcomes

`revive_attempt.result` records prompt delivery. It does not mean the agent resumed work.

Ordinary recovery saves an attempt and its observation before sending input.
`delivery` distinguishes `sent`, `queued`, `not_sent`, `unknown`, and `superseded`.
Only definite non-delivery is retried for the same error, after a delay and within
the three-attempt limit. A lost reply is `unknown`; it remains under observation
without another blind send, including after a later outage. Fresh checks skip changed sessions, new work, queues,
and pending approvals. bb uses its native request-guarded retry where available.

An outage stays pending until DNS readiness succeeds and its first recovery pass
completes. Pending work and attempts survive watcher restarts. Failed hosts and
targets do not stop the remaining scan; the normal recheck window covers later
attempts. DNS probes run in a bounded child process. Discord delivery runs in
the background, without making recovery wait for HTTP requests.

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
They are logged locally and sent to the diagnostics server only when telemetry
is enabled. Telemetry excludes session paths, thread IDs, prompts, and output text.

## Discord success alerts

When Discord is configured, every confirmed recovery uses the same success
notification path, including bb submission retries and daemon interruptions.
The message includes the host, agent, and thread title (or target reference when
no title is available). It says `Recovery confirmed`; sending or queueing input
alone does not qualify. The existing observation window still applies.

The watcher atomically saves the completed observation and its pending message
in `state.json`. Alerts remain pending across restarts, temporary HTTP failures,
and temporary loss of webhook configuration. Discord disabled at both dispatch
and confirmation creates no backlog. Existing attempt and error alerts remain
best effort; confirmed-success alerts have no memory-queue drop limit.

The delivery worker uses `wait=true` and requires a returned Discord message ID.
The watcher saves that receipt before removing the pending alert. Temporary
failures retry indefinitely with delays capped at five minutes; Discord's longer
`retry_after` takes precedence. Failed alerts rotate so newer alerts also get a
turn. Permanent request errors pause the affected alert; an invalid or inaccessible
webhook pauses all success delivery until the URL changes or a retry is requested.

`./install.sh status` shows the pending count, delivery errors, and last confirmed
delivery. Logs distinguish `discord_delivered`, `discord_delivery_failed`, and
`discord_delivery_paused`. Webhook URLs, tokens, and response bodies are never
included in delivery state or logs. The last 100 message receipts are retained.

After fixing webhook permissions or configuration, run `./install.sh discord-retry`.
This signals the watcher to retry saved alerts; it does not replay recovery commands.

Normal observation repeats and restarts after a saved acknowledgement do not
send duplicates. A crash or lost connection after Discord creates a message but
before the receipt is saved can cause a duplicate on retry. This is delivery with
retries, not an exactly-once guarantee. Deleting or corrupting local state also
falls outside that guarantee.

Protocol references: [Discord webhooks](https://docs.discord.com/developers/resources/webhook#execute-webhook)
and [rate limits](https://docs.discord.com/developers/topics/rate-limits#exceeding-a-rate-limit).

The telemetry server accepts at most 16 KiB per request and event, with a
nonblank event name of at most 128 characters. Stalled reads time out after
5 seconds. Logs rotate at 10 MiB with one backup (20 MiB total). `/stats`
counts retained events, skips corrupt records, and excludes rotated-out history.

Codex skips folders with multiple recent matching sessions (`ambiguous_session`).
It needs an unambiguous session before sending input.
