# Experiment 0015 — Cursor (acp) in bb revived after a real Wi-Fi cut

Date: 2026-09-03. Rerun of 0014 after adding the `ping timed out`
fingerprint. David flipped the Wi-Fi radio off by hand, lid open. Watcher
pid 3511 ran unattended under launchd. Human flip, so ADR 0037 does not apply.

Goal: unattended revive of a Cursor thread in bb that dies mid-task, and
only of threads that died in the outage.

## Setup

Same as 0014, project `immortal-agents`, local environment:

- `thr_rsgzd56nhq` Cursor TARGET (Grok 4.6 xhigh fast) — 60-section essay,
  `sleep 15` between sections
- `thr_eymtzkpz3k` Cursor CONTROL — replied `ready`, idle
- `thr_ufmnps2yhf` Claude Code REFERENCE (Fable 5.1) — same essay
- Capture: `experiments/0015-capture.sh` →
  `experiments/0015-captures/snapshots.log`. Staged at `21:42:29Z`.
- Both mid-task threads were on section 1–2 when the radio went off.

## Timeline (UTC)

- 21:43:23 `to_offline` `at` (last good probe), `detected_at` 21:43:33
- 21:43:56 TARGET dies, 33s in: `Error: RetriableError: [unavailable]
  PING timed out`, turn `completed`, thread `idle`
- 21:44:38–21:47:05 REFERENCE `Claude Code API retry 1/10 … 10/10`
- 21:47:38 REFERENCE dies: `API Error: Can't reach the API server …
  (ENOTFOUND)`, `provider/error`, turn `failed`, thread `error`
- 21:53:50 Wi-Fi on → `recovery`, duration 627s. `apis_ready
  waited_secs=0`
- 21:53:52 unrelated `thr_mav5365vr3` (Claude, died in window) `resume`,
  `resume_sent ok`, Discord sent
- 21:53:53 REFERENCE `resume` `all_three_agree`, `resume_sent ok`,
  Discord sent
- 21:53:54 TARGET `resume` `["status=error", "provider=acp-cursor",
  "network_error=True", "error_in_outage=True", "all_three_agree"]`,
  `resume_sent ok`, Discord "Revived: Cursor in bb" sent
- 21:53:54 unrelated `thr_p8gvhwz5kj` (Claude, died in window) revived
- 21:53:54 TARGET `keep going` → `turn/started`, status `active`
- 21:53:55 REFERENCE `turn/started`
- 21:54:55 recheck tick: no new resume. Both threads writing again
  (section 3 by 21:56).
- CONTROL never enumerated.

## Result

Live-proven (ADR 0034): Cursor CLI inside bb is revived unattended after a
real internet loss. Detection = idle thread + final agent message is the
CLI error line + inside the outage window. Revive = `bb thread tell … --mode
auto`. Control untouched. Four revives total, all of threads that died in
the window.

Fixture: `tests/fixtures/bb_thr_rsgzd56nhq_events.json` (death and revive).

## Notes

- Claude Code died this time (10 retries, ~3 min). In 0014 it survived 24
  min with 8 retries. The retry budget is time-boxed, not count-boxed;
  do not rely on it.
- Cursor dies fast (~33s) and always the same way: error line as the last
  message, turn `completed`, thread `idle`.
