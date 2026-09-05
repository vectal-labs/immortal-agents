# Experiment 0013 — Pi revive after a real Wi-Fi pause (cmux)

Date: 2026-09-03. First live cut aimed at Pi (`detect_pi.py`, commit
`8d5bf26`). The operator flipped the Wi-Fi radio off by hand. Watcher ran
unattended under launchd. Human flip, so ADR 0037 does not apply.

Goal: prove the watcher revives a Pi session that dies mid-task, and
revives only that one. Also record Pi's real death fingerprints.

## Setup

- cmux workspace `workspace:5` ("0013 pi"):
  - `surface:13` Pi A (kimi-k3 via OpenRouter) — target, mid-task
  - `surface:16` Pi control — idle (`ready`), must not be revived
  - `surface:17` Claude A (Fable 5.1) — same essay as a reference
- Both Pi panes shared cwd
  `docs/experiments/0013-captures/workdir`, so they share one Pi
  session folder.
- Watcher under launchd, probe 10s, threshold 120s, imports `detect_pi`.
- Capture: `docs/experiments/0013-capture.sh` →
  `docs/experiments/0013-captures/snapshots.log`.
- Staged at `2026-09-03T09:00:27Z`.
- Pi A finished the first essay just before the cut. A second 16-section
  Pacific-cable prompt was sent at ~09:08:17Z so it was `Working...`
  when Wi-Fi went off.

## Timeline (UTC)

- 09:08:17 Pi A gets the Pacific prompt (`last_user_ts` on its session)
- 09:08:30 probe=200. Pi A `Working...`. Control idle.
- 09:08:36 Wi-Fi off → watcher `to_offline`
- 09:08:50 probe=000000. Pi A still `Working...`
- 09:08:50–09:25:42 capture and watcher logs go quiet (~17 min gap)
- 09:25:42 / 09:26:02 still `Working...` (silent mid-stream wait)
- 09:26:22 Pi A first death on screen: `Error: terminated`, then
  `Error: Connection error.`, then `Retrying (2/3) in 4s...`.
  Session flush: `stopReason=error`, `errorMessage=Connection error.`
- 09:26:43 pane fingerprint:
  `Error: Retry failed after 3 attempts: Connection error.`
- 09:26:34 session last error ts (used at evaluate)
- 09:28:24 Wi-Fi on → probe=200, `recovery`, duration 1187s (~19.8 min)
- 09:28:24 `apis_waiting` `api.anthropic.com`
- 09:30:05 `apis_ready waited_secs=101`
- 09:30:05 Pi A `harness=pi` via `surface_title` (`π - workdir`).
  `decision=resume` (`pane=network_error`, `sessions=2`,
  `all_three_agree`) → ESC + `keep going` + Enter, `resume_sent ok`
- 09:30:06 Discord `notify` Pi `sent=true`
- 09:30:06 Claude A `decision=resume` (`all_three_agree`) → same
  keystrokes, `resume_sent ok`, Discord `notify` Claude `sent=true`
- 09:30:07 Pi control `decision=skip`
  (`pane=other`, `sessions=2`, `already_resumed`)
- 09:30:08 `recheck_armed` until 09:35:08
- 09:30:06 snapshot: `keep going` then Pi A `Working...` again
- 09:31:09 / 09:32:11 recheck ticks: Pi A and control skip. No new
  cmux revive. Three unrelated bb Claude threads decided `resume` on
  recheck (errors after reconnect, stale DNS) but `may_revive` blocked
  them — no `resume_sent`.

## Result

Pi in cmux is live-proven (ADR 0034): mid-stream death → fingerprint →
DNS wait → ESC + `keep going` → Pi A working again. Control never
revived.

## How Pi dies

This cut hit **mid-stream**, not at connect time. Pi stayed on
`Working...` for ~18 min after the radio went off. That matches the
known 10-min `retry.provider.timeoutMs` / `httpIdleTimeoutMs` hang,
then a fast 3-retry (~15s) and idle flush.

Pane fingerprints, in order:

- `Error: terminated`
- `Error: Connection error.`
- `Retrying (2/3) in 4s... (escape to cancel)` — `detect_pi` treats
  `retrying (` as still-alive (`classify_pane` → `other`)
- `Error: Retry failed after 3 attempts: Connection error.`

Session JSONL (written only when the turn ends and Pi returns to idle):

- `stopReason: "error"`
- `errorMessage: "Connection error."`
- timestamp inside the outage (`09:26:34Z`)

An empty session folder while Pi is mid-turn is expected.

## How the watcher saw Pi

`is_pi_pane(screen)` was **false**. The live footer is `4.5%/1.0M`,
not the coded marker `%/200k`, and the screen text has no `π -`.
Identification used the cmux title `π - workdir` (`surface_title`).
That was enough this run.

## False positives / misses

- **No miss on Pi A.** Resume landed; it started working.
- **No false positive on Pi control.** Saved by `pane=other`. The
  shared cwd means `evaluate()` reads the newest session for both
  panes. After Pi A was revived that session ended with a user
  `keep going`, so control also got `already_resumed`. If control had
  been first, the same dead session plus `pane=other` would still
  skip (`pane_not_network_error`). Risk only if the idle pane also
  showed a network-error fingerprint.
- **Claude A revived.** Correct: it died in the window
  (`Can't reach the API server`, retries). Not the control.
- **Two Discord lines** (Pi + Claude). Expected.

## Fix to consider in `detect_pi.py`

Not required for this pass. Optional:

1. Add a pane marker for the live footer (`%/1.0M` or a looser
   `%/` + model line) so identification does not depend on the cmux
   title.
2. Map pane → session instead of "newest jsonl in the cwd folder",
   so two Pi panes in one directory cannot share a death signal.

Do not weaken the three-signal rule to force a pass.

## Still not proven

A connect-time Pi death (fast error, no 10-min hang). A Pi control
that shares cwd *and* shows a network-error pane. Recheck re-revive
of Pi A after a second death (`MAX_REVIVES`).
