# Recovery reliability — 2026-09-05

## Assumptions and evidence

- A lost command reply can follow acceptance. Reserve attempts before dispatch; keep `unknown` separate from rejection and reconcile before another send. [AWS idempotent API guidance](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/) supports this distinction. A local reservation alone does not guarantee exactly-once provider effects.
- Delivery, progress, and completion are different facts. [OpenAI Agents SDK results](https://openai.github.io/openai-agents-python/results/) explicitly separates durable input occurrence from provider delivery guarantees. `sent` or `queued` must not become confirmed recovery without later assistant output.
- Status alone cannot identify an actionable failure. [Codex app-server](https://developers.openai.com/codex/app-server/) documents active approval waits and completed/interrupted/failed turn outcomes. [Claude's agent loop](https://code.claude.com/docs/en/agent-sdk/agent-loop) distinguishes success, execution errors, limits, and connection/process failures without a result message.
- Pending approvals require their normal resolution. [OpenAI's approval flow](https://openai.github.io/openai-agents-python/human_in_the_loop/) preserves and resumes paused state. Keeping outage work pending while readiness is unavailable, isolating host failures, and limiting retry attempts are engineering recommendations here; this research did not measure user preferences or prove a particular retry interval. [AWS retry guidance](https://docs.aws.amazon.com/sdkref/latest/guide/feature-retry-behavior.html) supports bounded attempts, backoff, and retry budgets.

## Research record

Three separate DeepAPI `/v1/research/deep` calls succeeded with `completeness: complete`:

- Safe retry after a lost reply: `c9b15f16-2a08-449e-aab7-fcb444f2f235`.
- Real asynchronous failure shapes: `ef9b31b8-df37-4e6d-928e-9abf6b888609`.
- Recovery versus human intervention: `1b327ca3-5c9d-4396-ac2a-7a62dddf61fd`.

The research returned both primary and secondary material; only primary sources support the conclusions above. A follow-up website scrape, `b070c090-0dc4-48a6-8c5c-5d5c8058e883`, returned all six linked pages. The Codex page was capped at 90,000 characters; the cited status and turn sections were present. Other pages were complete. No private transcripts or credentials were sent.

## Live read-only measurement

At 13:07:46–13:07:49 UTC, installed bb 0.42.0 supplied full histories, fresh thread details, queues, and interactions for 20 current, non-archived, in-scope threads. Selection prioritized errors, interactions, queued work, active work, then recency. The returned list contained 30 current in-scope rows; its 14 error-status rows were all excluded as archived. This bounded list is not a census.

- 19 Codex and 1 Claude thread; 3 active and 17 idle.
- 29,154 events; all 80 individual reads succeeded.
- 2 queued messages on 2 threads, both waiting for busy threads; 0 pending interactions.
- 16 historical provider errors across 3 threads; every error said `willRetry: true`.
- 1 historical rejected submission and 5 daemon interruptions across 3 threads. Later work superseded them.
- 208 requests and 207 acceptance events. All 207 acceptance events correlated to a recorded request through `clientRequestId`.
- No final actionable failure in any sampled history. Baseline eligibility: **0/20**. Candidate snapshot guard eligibility: **0/20**. No status or `updatedAt` changes occurred between list snapshots.
- Full-history read latency: median 431 ms, maximum 695 ms. Fresh thread-detail reads: median 961 ms, maximum 1,183 ms.

The candidate's actual `_resume_args` selector was replayed against captured responses with dispatch unavailable. Raw histories, collection scripts, source extracts, and per-case results remain in private thread storage. This report contains no thread identifiers or prompts.

## Installed bb retry contract and limits

The installed guide and bundled source agree: `retry --turn` asserts the latest failed request. Accepted input receives a continuation nudge; unaccepted input is resent. Acceptance is keyed by request ID after its request sequence. A reply contains `ok`, `delivery`, original `turnRequestId`, and attempt number; queued replies add queue identity and wait details. Concurrent retry and existing queued-retry checks prevent ordinary duplicate submissions.

**Residual race:** the installed server checks failed request identity before asynchronous dispatch preparation. Its final append transaction does not recheck that identity. Fresh caller checks and native retry reduce stale sends but do not establish an atomic guarantee. Cursor's idle-error fallback also lacks a server-side expected-request guard.

This live sample validates exclusions and real event shapes, not successful recovery, current error/approval cases, Cursor/Pi behavior, crash persistence, notification latency, or recovery after a live outage. No retries, messages, stops, network cuts, or real thread mutations were performed. Controlled integration results belong alongside this evidence before declaring the fixes verified.

## Controlled real-CLI run

At 13:18:35–13:19:12 UTC, **real Claude Code 2.1.261 recovered successfully** through the actual `immortal.sim.proxy`. The proxy first returned 502, then forwarded one streaming request to a deterministic local Anthropic-compatible server. Claude itself wrote the error and successful assistant JSONL records; the test did not fabricate them.

Six separate watcher processes demonstrated persisted state: offline; online with API readiness unavailable; restart while still unavailable; restart when ready; immediate output observation; and observation at the normal 30-second cadence. The actual Claude detector qualified all three signals. The actual revive/outcomes code recorded exactly **one reservation, one resume send, and one confirmed recovery**, with no pending observations left. Pending outage state survived both unavailable-readiness processes.

An earlier run exposed Claude's directory encoding mismatch for punctuation in workspace paths. After the detector fix, this run used the real generated directory without aliases.

Isolation: fresh Claude config/workspace/watcher state, dummy credential, `--bare --tools '' --strict-mcp-config`, and an OS localhost-only network sandbox. The native CLI stopped, every owned server closed, and both API ports refused connections afterward. No live watcher, real thread, app, or internet connection was changed.

Limits: the upstream response was simulated; a small HTTP-to-PTY adapter replaced GUI terminal transport. Probe/readiness inputs were local test controls, and minimum outage duration was accelerated to 0.1 seconds. This verifies the real CLI, proxy, detector, persistent recovery flow, and output confirmation together; it does not measure live provider reliability, real DNS recovery, GUI dispatch, or production notification delivery. Driver, raw terminal output, native JSONL, and report remain in private thread storage.

## Final regression result

All **338 tests passed** on Python 3.14 with `python3 -m unittest discover -s tests`,
without a caller-supplied watcher state directory. The baseline had 264 tests.
New coverage includes host failures, pending readiness, process crashes before
and after dispatch, fresh user intervention, definite versus uncertain delivery,
provider retry persistence, and shared duplicate protection across outages.
Readiness tests use actual controlled child processes; notification tests verify
that a stalled sender cannot block recovery. `git diff --check` passed.

Review also reproduced and fixed a first-pass crash losing pending work, a failed
reservation leaving an unsent attempt blocked in memory, retry delays consumed
by slow scans, and legacy identityless entries being sent again. Production
installation and push were not performed.
