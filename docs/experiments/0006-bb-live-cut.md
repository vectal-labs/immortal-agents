# Experiment 0006 — First live revive of bb threads (real cut)

Date: 2026-09-01. Live proof (ADR 0034) for bb as second host (ADR 0036).
The operator explicitly asked the agent to stage and run the cut itself this time,
superseding ADR 0020's "David launches" rule for this session only.

## Setup (bb project offline-agent-restart, all chat-only essays)

- `thr_e06claude` — Claude Code, 10-part essay, mid-task at cut → expect `resume`
- `thr_e06codex1` — Codex, same essay, mid-task at cut → expect `resume`
- `thr_e06ctrl01` — Codex control, one sentence, finished (`idle`) → expect not listed
- `thr_e06agent1` — this agent (acp-cursor), will die → out of scope, expect `skip`
- Older `error` threads (`thr_claudea01` etc.) → expect `skip`, `timing_miss`

Watcher daemon running the new `host_bb.py` code. No cmux test panes.

Cut: `OUTAGE_SECS=480 docs/experiments/0001-run-outage.sh`, armed detached
with a 30s fuse. Timeline and results are filled in after recovery from
`~/.offline-agent-restart/watcher.log` and `docs/experiments/outage-runs/`.

## Timeline (UTC, from the watcher log)

- 21:35:18 cut (run 1 of the outage script, 480s window)
- 21:39:23 `thr_e06claude` (Claude Code) dies: `API Error … ENOTFOUND`
- 21:43:34 recovery after 496s; watcher enumerates 4 `error` threads
- 21:43:36 decision `resume` on `thr_e06claude` (`all_three_agree`); `bb thread tell` fails: `HTTP 409: Thread is not active`
- 21:43:52 launchd restarts the cut job → Incident 0001 (second cut, manual Wi-Fi restore at 22:00:33)

## Decisions

- `thr_e06claude` Claude Code, mid-task → `resume` (correct)
- `thr_claudea01` old network error → `skip`, `timing_miss` (correct)
- `thr_olderr001`, `thr_login0001` non-network errors → `skip`, `error_not_network` (correct)
- `thr_e06codex1` Codex never entered `error` → not listed (correct)

## Findings

- bb detection works: one resume, three skips, zero false positives.
- bb revive failed with HTTP 409. Fix: `bb thread tell … --mode auto` (Incident 0001, proven live in 0007).
- Codex inside bb survived the 8-minute cut on its own (see 0007, finding 2).
- The cut mechanism (`launchctl submit`) caused Incident 0001. Read it before touching anything network-related.
