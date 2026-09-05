# 0036 — bb as second host (cmux + bb)

## Context

bb (getbb.app) is a GUI that runs Claude Code and Codex as provider sessions,
not terminals. It records an internet death structurally: a `provider/error`
event with the network error text, a `turn/completed` with `status: failed`,
and thread `status = error`. Finished or waiting threads are `idle`. A real
death was already recorded on this Mac on 2026-08-31 (`thr_claudea01`).

## Decision

bb is a second host next to cmux — same two harnesses, different plumbing
(David, 2026-09-01):

- Read state via the `bb` CLI JSON (`bb thread list`, `bb thread log`).
- Death = thread `status == error` + last `provider/error` detail matches a
  network fingerprint + error timestamp inside the outage window (mirrors
  ADR 0033).
- Scope: `claude-code` and `codex` provider threads only.
- Revive = `bb thread tell <id> "keep going"`.
- Live proof (ADR 0034) is one real machine-wide cut, launched by David.
- Code lands flat as `host_bb.py`; the ADR 0035 module split stays a separate
  task.

## Consequences

- One recovery pass now covers cmux panes and bb threads.
- No pane text or session-log mapping needed for bb.
- bb server down means nothing to revive: log `bb_unavailable` and skip.
