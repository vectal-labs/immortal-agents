# Recovery outcomes

`revive_attempt.result` records prompt delivery. It does not mean the agent resumed work.

Ordinary recovery saves an attempt and its observation before sending input.
`delivery` distinguishes `sent`, `queued`, `not_sent`, `unknown`, and `superseded`.
Only definite non-delivery is retried for the same error, after a delay and within
the three-attempt limit. A lost reply is `unknown`; it remains under observation
without another blind send, including after a later outage. Fresh checks skip changed sessions, new work, queues,
and pending approvals. bb uses its native request-guarded retry where available.

The legacy outage path stays pending until DNS readiness succeeds and its first recovery pass
completes. Pending work and attempts survive watcher restarts. Failed hosts and
targets do not stop the remaining scan; the normal recheck window covers later
attempts. DNS probes run in a bounded child process. Discord delivery runs in
the background, without making recovery wait for HTTP requests.

BB also scans final failures every 30 seconds independently of Apple's result.
For an identified local provider endpoint, a connection failure aged 2–30 minutes
can use the same guarded retry after an unauthenticated HTTPS HEAD probe succeeds.
One provider cannot block another. A failed or unknown probe consumes no attempts.
The endpoint check also guards the legacy BB outage path; Apple's recovery cannot
override an unreachable provider. Unknown endpoints keep the existing outage policy.

Codex routes come from BB's latest session ID, the local Codex session index,
configuration, and auth mode. ChatGPT subscriptions use the Codex `/responses`
route, not the public OpenAI API. Explicit Claude endpoints are supported; Pi,
Cursor, remote machines, layered/ambiguous config, and explicit proxy routes do
not receive an inferred endpoint. BB does not expose its complete inherited
provider environment, so hidden launch overrides remain a limitation.

HEAD responses 2xx, 400, 401, 404, and 405 demonstrate contact, not model readiness.
Redirects and 403 remain unknown; 429, server errors, and connection failures defer
recovery. Certificates are verified; redirects are not followed. Probes never
send credentials, prompts, or inference requests. Explicit loopback APIs may use HTTP.
Each endpoint worker expires after 15 seconds, even if its thread disappears.
Results are cached for 45 seconds to span the 30-second scan; at most eight probes
run concurrently. Apple's DNS and HTTP check has a six-second total deadline.

Local logs distinguish `endpoint_probe` results and `endpoint_unknown` /
`endpoint_unreachable` waiting decisions. Endpoint logs include only the host,
status, and fixed reason; configuration and credentials are not logged.

After delivery, the watcher checks for new assistant output or tool activity in
the same session every 30 seconds, including while Apple's check fails. There is
no time limit. Pending observations survive restarts and temporarily unavailable logs:

- `revive_confirmed`: new assistant output was observed after the prompt.
- `revive_unconfirmed`: the matched turn ended without progress, the session was
  replaced, another retry superseded the attempt, or no session log was identifiable.

BB matches the retry request or resume input to its accepted turn. Queued follow-ups
do not supersede a running recovery; different accepted input does. New assistant
text or tool activity counts as output. User input, old output, tool results alone,
and recognized error messages do not confirm recovery. CLI observations retain a
file identity and byte cursor, retry partial writes, and ignore automatic context
and compaction summaries. Checks never send another prompt.
Confirmation proves observed activity, not task completion or causation.

Both events include an attempt ID, host, harness, reason, and elapsed seconds.
They are logged locally and sent to the diagnostics server only when telemetry
is enabled. Telemetry excludes session paths, thread IDs, prompts, and output text.

## Reconnect steering (ADR 0052)

After every reconnect, and after the Mac wakes, the watcher steers one `keep going`
into each BB thread that was still active at that moment. No error and no minimum
outage is required; the ordinary failed-thread recovery above is unchanged and runs first.

An episode opens on an offline-to-online probe or when wall time outruns
`time.monotonic()` by more than five seconds (the Mac slept; a slow scan never
qualifies). The reconnect that follows a wake gap joins that wake episode once.
The candidate set is frozen at the episode start: local, in-scope providers
(`claude-code`, `codex`, `pi`, `acp-cursor`), status `active`, not archived or
deleted, not waiting on a question or approval, and not retried by ordinary
recovery since that tick's probe (older unconfirmed attempts do not count). Work
started later is not part of that reconnect. If BB cannot answer
yet, the snapshot is retried on later ticks instead of freezing an empty set.

Each candidate is sent once its provider answers: known routes use the same
unauthenticated endpoint probe as recovery, unknown routes use the DNS readiness
check. Cached results from before the outage are discarded when the episode opens.
Nothing is sent while the Apple probe is offline. Right before the send the thread
is re-read; a finished thread or a pending interaction (`bb thread interactions
list`, since `thread show` omits `hasPendingInteraction`) cancels its nudge.
`bb thread tell --mode auto --json` reports `sent` or `queued`.

The episode, its candidates, and each reservation are saved in `state.json` before
bb is called, so polls, crashes, and watcher restarts cannot repeat a nudge. A later
distinct reconnect is a new episode and steers again. Episodes expire after 15 minutes.
Logs: `steer_episode` (opened, snapshot, merged, expired) and `steer_sent`. Steers
are not confirmed or announced on Discord.

Limits: threads on other machines are never steered; unknown routes wait for both
`api.anthropic.com` and `api.openai.com` to resolve; a wake is only seen while the
watcher process survives it (a restart is not a wake).

## Discord success alerts

Every confirmed recovery uses the same durable success
notification path, including bb submission retries and daemon interruptions.
The message includes the host, agent, and thread title (or target reference when
no title is available). It says `Recovery confirmed`; sending or queueing input
alone does not qualify.

The watcher atomically saves the completed observation and its pending message
in `state.json`. Alerts remain pending across restarts, temporary HTTP failures,
and missing webhook configuration. Successes observed without a configured webhook
remain queued and are delivered after one is configured. Existing attempt and error alerts remain
best effort; confirmed-success alerts have no memory-queue drop limit.

The delivery worker uses `wait=true` and requires a returned Discord message ID.
The watcher saves that receipt before removing the pending alert. Temporary
failures retry indefinitely with delays capped at five minutes; Discord's longer
`retry_after` takes precedence. Failed alerts rotate so newer alerts also get a
turn. Permanent request errors pause the affected alert; an invalid or inaccessible
webhook pauses all success delivery until the URL changes or a retry is requested.

`./install.sh status` shows the pending count, delivery errors, and last confirmed
delivery, plus recoveries awaiting progress and unavailable observation sources.
Logs distinguish `discord_delivered`, `discord_delivery_failed`, and
`discord_delivery_paused`. Webhook URLs, tokens, and response bodies are never
included in delivery state or logs. The last 100 message receipts are retained.

After fixing webhook permissions or configuration, run `./install.sh discord-retry`.
This signals the watcher to retry saved alerts; it does not replay recovery commands.

Normal observation repeats and restarts after a saved acknowledgement do not
send duplicates. A crash or lost connection after Discord creates a message but
before the receipt is saved can cause a duplicate on retry. This is delivery with
retries, not an exactly-once guarantee. Deleting or corrupting local state also
falls outside that guarantee.

## Native Codex recovery

The watcher also reads Codex's local SQLite logs. It correlates native retry warnings
with assistant/tool progress in that exact native session and turn. New builds with
the experiment 0021 patch save content-free `recovery_started`, `recovery_confirmed`
and terminal `recovery_unconfirmed` records with a stable recovery ID, including
`codex exec --ephemeral`. These awaited SQLite writes bypass the diagnostic logging
buffer and its verbosity setting. Recovery records are exempt from diagnostic-log
retention limits so a paused watcher can catch up. Valid completed compaction also
counts as model progress. Older binaries sharing the same Codex home still apply
their original retention rules. These records do not contain prompts, output, tool
arguments or credentials. Older interactive
builds use retry warnings plus rollout history; older exec builds without these
records cannot report native recovery through this source.

The default source is `CODEX_HOME`, otherwise `~/.codex`. Additional homes can be
provided through `IMMORTAL_CODEX_HOMES` (colon-separated paths on macOS). The first
scan starts at existing log history's end; it does not replay old incidents. Later
events, pending observations and confirmations survive watcher restarts. If the
database does not yet exist, the first new recovery is observed. Native
checkpoints and queued alerts are saved together. Watcher/native correlations
prevent reporting the same recovery twice, including delayed diagnostic writes.
BB turn IDs are mapped to native IDs where available; otherwise deduplication uses
the matched accepted-request interval in the same provider session.

Source events must be available locally. Missing/deleted state, inaccessible custom
Codex homes and older non-reporting binaries remain explicit coverage limits.

Protocol references: [Discord webhooks](https://docs.discord.com/developers/resources/webhook#execute-webhook)
and [rate limits](https://docs.discord.com/developers/topics/rate-limits#exceeding-a-rate-limit).

The telemetry server accepts at most 16 KiB per request and event, with a
nonblank event name of at most 128 characters. Stalled reads time out after
5 seconds. Logs rotate at 10 MiB with one backup (20 MiB total). `/stats`
counts retained events, skips corrupt records, and excludes rotated-out history.

Codex skips folders with multiple recent matching sessions (`ambiguous_session`).
It needs an unambiguous session before sending input.
