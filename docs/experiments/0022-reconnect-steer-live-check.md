# 0022 — Reconnect steer against live BB threads

2026-09-10. ADR 0052 implementation checked against real BB threads from an isolated
state directory. No watcher was installed or restarted; David's threads were not touched.

## Active thread receives the steer

Disposable Codex thread `thr_rt3y7m3ngn` ran a foreground `sleep 170`.
`immortal.hosts.bb.list_active()` returned it among 13 eligible active threads;
`steer()` returned `sent`. Its event log then showed, inside the same turn
`da6990c326-t1` as the sleep:

```
32 client/turn/requested  source=tell  input="keep going"
33 turn/input/accepted    turnId=da6990c326-t1
```

The agent replied "The sleep command is still running." and later "done".

## Thread waiting on a question is skipped

Disposable Claude Code thread `thr_5x3p94yv2v` was blocked on `AskUserQuestion`.
`bb thread list` reported `hasPendingInteraction: true`; `bb thread show` omitted
the field; `bb thread interactions list` returned one row with `status: "pending"`.
`list_active()` excluded the thread and `steer()` returned `superseded`; the log
recorded zero `tell` requests.

## Claude Code cannot be a sleep fixture

A Claude Code fixture refused a foreground `sleep` as a blocking command. Use Codex
for live turn fixtures.

Both fixtures were archived afterwards.

## v0.2.0 candidate verification (2026-09-11)

What was checked for the release that ships this steering, with provenance:

- Unit suite: `python3.14 -m unittest discover -s tests` on the candidate commit
  ran 619 tests, OK, including the 33 reconnect and wake tests in
  `tests/test_reconnect_steer.py`.
- Old-updater upgrade path (`0022-upgrade-check.py`): a real clone of public
  v0.1.0 (`6f92cdc`) was upgraded to the candidate by the v0.1.0 updater code with
  a real Git tag fetch and fast-forward. GitHub, launchctl, and the watcher restart
  were mocked as in `tests/test_updates.py`; no watcher ran and no host adapter was
  touched. `state.json`, `telemetry`, `discord_webhook`, and `bb_runtime.json` were
  byte-identical afterwards. The candidate's same-version update then ran the
  fresh-process component migration, which reported that Codex recovery is not
  enabled and created nothing under the fixture home. A clone at the initial
  public commit, which predates the checker, fast-forwarded with `git pull --ff-only`.
- Real watcher restart: `./install.sh restart` and `./install.sh status` on the
  primary checkout of David's Mac after the candidate landed (see the release
  report for the exact PID and commit).

Not verified, and the reason: a physical sleep and wake, and a fresh install or
upgrade on a second Mac. The only other machines enrolled in BB are two Ubuntu
hosts, and forcing sleep on David's working Mac needs fresh authorization. The
live steer and pending-question evidence above stands for the unchanged steering
code; David's live watcher log held no `steer_episode` or `steer_sent` events to
substitute for a real wake.
