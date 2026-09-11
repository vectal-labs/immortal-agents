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
