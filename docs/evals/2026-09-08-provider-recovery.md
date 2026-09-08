# BB recovery while Apple's internet check fails

## Failure and scope

The September 8 `local cloud handoff` turn failed during Codex compaction at
16:42:59 UTC. Apple first passed at 16:59:04; Immortal retried at 16:59:21.
The log establishes the gate that delayed recovery, not when Codex became reachable.

The trimmed fixture `tests/fixtures/bb_compaction_connection.json` retains the
request identity, accepted input record, retry notices, and final connection error.
It excludes the original prompt. Before the change, replaying it through the
watcher with Apple false and provider reachable produced zero retry commands.

David approved implementation of independent BB endpoint checks on September 8.
This is a narrow exception to the historical frozen-watcher rule; no old ADR was
rewritten. Existing terminal recovery, retry reservations, and confirmation remain.

## Design evidence

Three separate DeepAPI researches examined dependency checks, realistic network
failures, and safe coding-agent retries. Findings: a successful DNS lookup is
insufficient; reachability does not establish authentication/model readiness;
bounded retries must preserve unknown delivery and user cancellation.

Primary references: [AWS retry guidance](https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/),
[safe retries](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/),
[HTTP response semantics](https://www.rfc-editor.org/rfc/rfc9110.html).

Read-only local measurement of ten recent Codex session records found ten using
the `openai` model provider. The incident's current session plus local auth metadata
resolved to `https://chatgpt.com/backend-api/codex/responses`.
BB's local host identity is persisted at `${BB_DATA_DIR}/host-id`; local metadata
is not applied to a different machine's threads.

## Ten real unauthenticated HEAD requests

Measured on this Mac, with no model requests, credentials, or network changes:

- ChatGPT `/backend-api/codex`: 403, unknown, 0.09 seconds.
- ChatGPT `/backend-api/codex/responses`: 405, reachable, 0.17 seconds.
- ChatGPT `/`: 403, unknown, 0.11 seconds.
- OpenAI `/v1`: 404, reachable, 0.22 seconds.
- OpenAI `/v1/models`: 401, reachable, 0.23 seconds.
- Anthropic `/`: 404, reachable, 0.18 seconds.
- Anthropic `/v1/messages`: 405, reachable, 0.06 seconds.
- Apple HTTPS captive-check page: 200, reachable, 0.15 seconds.
- OpenAI deliberately nonexistent path: 404, reachable, 0.25 seconds.
- Anthropic deliberately nonexistent path: 404, reachable, 0.20 seconds.

This ruled out probing the ChatGPT homepage or Codex base path. It also confirmed
that requiring HTTP 200 would reject working provider routes. These are point-in-time
connectivity measurements, not provider health guarantees or outage-frequency estimates.

## Regression coverage

The watcher replay covers independent provider failure, Apple-only failure, fresh,
stale, and future errors, queues, approvals, user stops, completion, lost CLI replies,
restart deduplication, and exact-turn output confirmation. Apple becoming reachable
cannot bypass a failed provider probe.

Real loopback HTTP tests cover authentication/method responses, server failures,
redirect refusal, TLS failure, shared probe caching, concurrent endpoints, child
cleanup after a thread disappears, and Apple's whole-process timeout. A real
DNS-stalled worker must terminate on its own after the production 15-second limit.

Independent review found and fixed the legacy-path bypass and orphaned-probe risk.
The cache also spans a full scan interval, so maintenance cannot expire every
successful result before BB consumes it.

## Final validation

- Full suite: `PATH=/opt/homebrew/bin:$PATH python3 -m unittest discover -s tests`
  passed 510 tests in 71.652 seconds. The final endpoint/local-machine test file
  also passed all 17 tests after adding its explicit remote-host guard case.
- Python 3.9 can still import the watcher and resolver; configurations requiring
  unavailable TOML support return unknown rather than preventing startup.
- The real incident thread's route was resolved again through the BB adapter and
  returned HEAD 405, reachable, without sending it a message or retry.
- Two disposable BB instances with the installed Codex executable passed the
  controlled connection-failure experiment. Production retry delays were unchanged.
- Final run: Apple remained false throughout; the unavailable provider consumed
  zero attempts. After restoring the loopback provider, BB recorded exactly one
  retry, one accepted input, one new `RECOVERY_OK` assistant message, and one
  `revive_confirmed`. Retry occurred 228.399 seconds after the original failure,
  including the deliberately extended provider outage and normal scan intervals.
- The final run exercised maintenance collecting a successful probe roughly 28
  seconds before the next scan, proving the cache survives that interval.
- Both runs left the Codex binary unchanged and closed all three test ports.

Reproduce with [the isolated BB driver](../experiments/0020-provider-recovery.py).
It uses a local model-response fixture, not OpenAI inference. Raw BB state stays
in a private temporary directory because it can contain machine credentials.

## Limits

Routes with unknown effective configuration retain the legacy recovery policy.
BB's full inherited provider environment is not exposed; hidden launch overrides
cannot be fully reconstructed. Explicit proxy and alternate Claude backend routes
are not guessed. A retry accepted by BB is only confirmed after new assistant output.
