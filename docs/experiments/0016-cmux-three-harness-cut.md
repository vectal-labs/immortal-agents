# Experiment 0016 — Claude Code, Codex, and Pi revived in cmux

Date: 2026-09-04. Real Wi-Fi cut on commit `087d3b3`. David flips the
Wi-Fi radio off and on by hand under ADR 0020. The launchd watcher runs
unattended from the primary checkout.

## Setup

- cmux workspace `workspace:9` (`0016 live cut`), shared cwd
  `/tmp/oar-exp16-live`:
  - `pane:20` / `surface:20` — Claude Code target, navigation essay
    with critique-and-rewrite cycles through v13, chat only
  - `pane:21` / `surface:21` — Codex target, same task
  - `pane:22` / `surface:22` — Pi target, same task restaged through
    v60 after its first run completed before the cut
  - `pane:23` / `surface:23` — Claude Code control, completed `ready`
    and idle at the prompt
- Watcher pid 51266, probe interval 10s, threshold 120s.
- cmux socket control mode: `automation`.
- Capture: `experiments/0016-capture.sh` →
  `experiments/0016-captures/snapshots.log`, every 20s for 40 minutes.
- Final restage at `2026-09-03T22:49:03Z`.

## Expected decisions

- Claude Code target: `resume` after its pane network error, JSONL API
  error, and outage timing agree.
- Codex target: `resume` after its pane network error and task completion
  inside the outage agree.
- Pi target: `resume` after its pane network error and session network
  error inside the outage agree.
- Claude Code control: `skip` because it finished cleanly before the
  outage and is idle at the prompt.

## Timeline

- `2026-09-03T22:49:39.033485Z` — connectivity was lost. The watcher
  detected the offline transition at `22:49:49.113769Z`.
- `22:50:53.460Z` — Claude Code recorded `Connection lost
  mid-response`.
- `22:51:08.087Z` — Pi ended with `Connection error` after its retries.
- `22:54:43.919Z` — Codex was still reconnecting. Its active turn never
  wrote a `task_complete` event.
- `23:10:54.524164Z` — connectivity recovered after 1,275.49 seconds
  (21 minutes 15.49 seconds). API readiness took zero additional
  seconds.
- `23:10:54.723395Z` — Claude Code target: `resume` because pane,
  session, and outage timing agreed. `keep going` was sent successfully
  at `23:10:55.216253Z`.
- `23:10:55.641608Z` — Claude Code control: `skip`,
  `finished_cleanly`.
- `23:10:55.694538Z` — Pi target: `resume` because pane, session, and
  outage timing agreed. `keep going` was sent successfully at
  `23:10:56.168654Z`.
- `23:10:56.565651Z` — Codex target: `skip`. The pane showed a network
  error, but the rollout had no `task_complete` inside the outage.
- `23:10:57.713756Z` — one unrelated bb Claude Code thread was also
  correctly resumed after an in-window `ENOTFOUND` network failure.
- By `23:11:19Z`, Claude Code and Pi were producing work after the
  injected `keep going`. Codex was also producing work, but had
  self-recovered without a watcher resume. The control remained idle.

## Result

Partial pass. The watcher revived the Claude Code and Pi targets. It
correctly skipped the finished Claude Code control. It missed the Codex
target because Codex did not write `task_complete` while its stream was
stuck reconnecting; Codex later recovered by itself.

There were no false-positive resumes. The extra bb Claude Code resume
was unrelated to the four-pane experiment, but its network error was
real and occurred during the outage. Experiment 0016 therefore proves
watcher revival for Claude Code and Pi, but not for Codex.
