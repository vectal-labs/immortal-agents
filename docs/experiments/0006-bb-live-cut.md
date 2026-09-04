# Experiment 0006 — First live revive of bb threads (real cut)

Date: 2026-09-01. Live proof (ADR 0034) for bb as second host (ADR 0036).
David explicitly asked the agent to stage and run the cut itself this time,
superseding ADR 0020's "David launches" rule for this session only.

## Setup (bb project offline-agent-restart, all chat-only essays)

- `thr_mgefhujx6e` — Claude Code, 10-part essay, mid-task at cut → expect `resume`
- `thr_ezg9m2ps6f` — Codex, same essay, mid-task at cut → expect `resume`
- `thr_dauzdfy247` — Codex control, one sentence, finished (`idle`) → expect not listed
- `thr_386368uc3x` — this agent (acp-cursor), will die → out of scope, expect `skip`
- Older `error` threads (`thr_old_1` etc.) → expect `skip`, `timing_miss`

Watcher daemon pid 95826 running the new `host_bb.py` code. No cmux test panes.

Cut: `OUTAGE_SECS=480 docs/experiments/0001-run-outage.sh`, armed detached
with a 30s fuse. Timeline and results are filled in after recovery from
`~/.offline-agent-restart/watcher.log` and `docs/experiments/outage-runs/`.

## Timeline (UTC, from the watcher log)

- 21:35:18 cut (run 1 of the outage script, 480s window)
- 21:39:23 `thr_mgefhujx6e` (Claude Code) dies: `API Error … ENOTFOUND`
- 21:43:34 recovery after 496s; watcher enumerates 4 `error` threads
- 21:43:36 decision `resume` on `thr_mgefhujx6e` (`all_three_agree`); `bb thread tell` fails: `HTTP 409: Thread is not active`
- 21:43:52 launchd restarts the cut job → Incident 0001 (second cut, manual Wi-Fi restore at 22:00:33)

## Decisions

- `thr_mgefhujx6e` Claude Code, mid-task → `resume` (correct)
- `thr_old_1` old network error → `skip`, `timing_miss` (correct)
- `thr_old_2`, `thr_old_3` non-network errors → `skip`, `error_not_network` (correct)
- `thr_ezg9m2ps6f` Codex never entered `error` → not listed (correct)

## Findings

- bb detection works: one resume, three skips, zero false positives.
- bb revive failed with HTTP 409. Fix: `bb thread tell … --mode auto` (Incident 0001, proven live in 0007).
- Codex inside bb survived the 8-minute cut on its own (see 0007, finding 2).
- The cut mechanism (`launchctl submit`) caused Incident 0001. Read it before touching anything network-related.
