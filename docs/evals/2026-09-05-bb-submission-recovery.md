# bb submission recovery validation

## Assumptions and evidence

- **A rejected instruction can be retried intact.** Confirmed in the installed bb `turn-retry.ts` implementation and a live disposable thread: the original marker returned after retry.
- **An errored runtime can be released without losing its failed request.** Live `bb thread stop` preserved `status=error`; `bb thread retry --turn` succeeded afterward.
- **Timeout does not establish non-delivery.** bb can return `delivery=sent` before the provider rejects startup. Recovery therefore checks request identity and queues, reserves before dispatch, and does not blindly repeat an unchanged error after a lost reply.

This follows the request-identity guidance in [AWS's idempotency article](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/)
and bounded retry guidance in [gRPC](https://grpc.io/docs/guides/retry/).
Permanent errors and cancellation should escape automatic retry; see
[Microsoft's retry pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/retry).

Three separate DeepAPI research requests covered mature retry policies, failure
modes, and user expectations: `a46d5dec-f98e-474f-b374-5f0b77fc412b`,
`1c0a014c-3cee-4574-8c98-ee4a772752cc`, and `29d5372f-e2b0-47c9-a198-cc5ee2250ee4`.
No defensible universal failure-frequency distribution was found; the local
measurement below is the evidence for this installation.

## Twenty real thread histories

Read 20 current bb thread histories: 12 errored, seven idle, one active.
This sample deliberately included failures; it is not a random traffic sample.

- New timeout recovery candidates: **1**, the reported `remove_tokenmaxx` failure.
- New retry candidates among the other 19 histories: **0**.
- Existing 404, configuration, and internet-outage errors did not enter the new submission retry path.
- A separate disposable process-exit failure was classified as unhandled, not an automatic timeout retry.

The old parser missed the reported submission error. The new parser extracts
its timestamp and request identity. Structured snapshots remain in the private
investigation's thread storage; full private transcripts are not committed.

## Behavior checks

All **243 tests passed**, including 28 new recovery checks. The regression test first failed against the old code with zero retry calls.
Coverage includes backoff, restart persistence, three attempts, reset failure,
queued/cancelled retries, lost replies, newer work, pending approvals, stale
errors, unhandled errors, and output from the exact retried turn.

Use the watcher's Homebrew Python, with isolated state:

```sh
WATCHER_STATE_DIR=$(mktemp -d /tmp/immortal-tests.XXXXXX) /opt/homebrew/bin/python3 -m unittest discover -s tests
```

The macOS system Python on this machine lacks `unittest.TestCase.enterContext`,
which the existing suite requires. No tests were weakened for that interpreter.

## Live recovery

The disposable thread produced a real 30-second JSON-RPC timeout with no network
cut. The full recovery pass used actual bb reads and commands, restricted to that
test thread. It submitted the original input and confirmed matching assistant
output 11.4 seconds after tracking began. See [experiment 0018](../experiments/0018-bb-submission-recovery.md).
