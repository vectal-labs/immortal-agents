# Experiment 0014 — Cursor (acp) in bb, real Wi-Fi cut

Date: 2026-09-03. First live cut aimed at Cursor CLI inside bb (ADR 0048)
and at the window-start fix (window opens at the last good probe). David
flipped the Wi-Fi radio off by hand, lid open. Watcher ran unattended under
launchd on the new code. Human flip, so ADR 0037 does not apply.

Goal: prove the watcher revives a Cursor thread in bb that dies mid-task,
and revives only that one.

## Setup

- bb project `immortal-agents`, local environment, three threads:
  - `thr_acbjnabb28` Cursor TARGET (Grok 4.6 xhigh fast) — 60-section
    essay, one file per section, `sleep 15` between sections
  - `thr_z6jvwus2pi` Cursor CONTROL — replied `ready`, idle
  - `thr_jx9bqzrmax` Claude Code REFERENCE (Fable 5.1) — same essay
- Watcher pid 20739, probe 10s, threshold 120s, Cursor detection on.
- Capture: `experiments/0014-capture.sh` →
  `experiments/0014-captures/snapshots.log`. Staged at `20:40:27Z`.
- Both mid-task threads were at section 5 of 60 when the radio went off.

## Timeline (UTC)

- 20:43:06 last good probe
- 20:43:16 `to_offline` `at` (= last good probe tick), `detected_at`
  20:43:26. Window-start fix confirmed.
- 20:43:50 TARGET dies, 34s into the cut. Last agent message:
  `Error: RetriableError: [unavailable] PING timed out`. Turn ends
  `completed`, thread `idle`. No `provider/error`.
- 20:44:34–21:06:55 REFERENCE logs `Claude Code API retry 1/10 … 8/10`
  with backoff. Never dies. Status stays `active`.
- 21:07:11 Wi-Fi on → `recovery`, duration 1435s (~24 min, David kept
  it off longer than the planned 10). `apis_ready waited_secs=0`.
- 21:07:12 bb targets: TARGET (`bb_status=idle`, `status=error`) and the
  old `thr_vp7hipzyr6`. TARGET `decision=skip`
  `["network_error=False", "error_not_network"]`. Old thread
  `timing_miss` (correct).
- 21:07:23 provider pass: TARGET `decision=unknown`
  `error_not_whitelisted` → Discord "Unhandled provider error: Cursor in
  bb" `sent=true`.
- 21:07:44 REFERENCE resumes on its own (retry 9 succeeds), keeps writing.
- 21:08:03 Manual `hosts.bb.resume("thr_acbjnabb28")` → `bb thread tell
  … --mode auto` ok → `turn/started`, TARGET `active`, writing again.
- CONTROL never enumerated. No other revives.

## Result

Near miss. Every stage worked except one fingerprint:

- Window opens at the last good probe: proven.
- Dead idle Cursor thread is enumerated as a target: proven.
- `bb thread tell --mode auto` revives an idle Cursor thread: proven
  (by hand).
- `ping timed out` was not in `NETWORK_FINGERPRINTS`, so the outage pass
  skipped it. Added after this run, with the thread log as fixture
  `tests/fixtures/bb_thr_acbjnabb28_events.json`.

Not yet live-proven end to end (ADR 0034): an unattended revive of a
Cursor thread. Needs one more cut.

## How Cursor dies (bb)

- Fast: 34s after the radio went off, during a `sleep 15` tool call.
- Fingerprint this run: `Error: RetriableError: [unavailable] PING timed
  out`. Earlier the same day (lid closed): `Error: RetriableError:
  Connection stalled`. Exp 0011 (TUI): `RetriableError: [internal]`.
- Always: error as the final agent message, `turn/completed status=
  completed`, thread `idle`. Never `status=error`, never `provider/error`.

## Claude Code did not die

Claude Code retried 8 times over 24 minutes (512ms → ~36s backoff, then
long waits) and resumed by itself when the network returned. Earlier
experiments saw it die at ~70s; the retry budget has grown. The watcher
correctly did nothing.

## Next

Rerun the same setup once. Expected: TARGET `decision=resume
all_three_agree`, `resume_sent ok`, Discord "Revived: Cursor in bb".
