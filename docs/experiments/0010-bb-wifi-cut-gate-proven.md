# Experiment 0010 — bb revive after a real Wi-Fi cut; gated loop proven, DNS wait never fired

Date: 2026-09-02. Third real cut for the bb host (ADR 0036), first with the
0009 code (readiness gate + recheck). David flipped Wi-Fi off by hand for
8.5 min. Watcher ran unattended under launchd. Human flip, so ADR 0037 does
not apply.

## Setup

- bb project `offline agent restart`, three threads: `0010 Claude A`
  (claude-code) and `0010 Codex A` (codex) writing a long essay (transatlantic
  cables, mid-stream when the cut hit), plus a `control` thread (idle).
  Prompts were sent by a `main` acp-cursor thread in the same project.
- Unplanned: David messaged `other-thread-title`
  (claude-code, project `other-project`) 10s before the cut.
- Discord webhook configured. cmux, Terminal.app, Ghostty not running.

## Timeline (UTC)

- 21:36:15 Claude A and Codex A get the essay prompt
- 21:36:57 David messages the `other-project` thread
- 21:37:07 Wi-Fi off → watcher `to_offline`
- 21:38:12 Claude A `Claude Code API retry 1/10`
- 21:41:02 `other-project` thread: retry 10/10 failed, status `error` (ENOTFOUND)
- 21:41:07 Claude A: retry 10/10 failed, status `error` (ENOTFOUND)
- 21:42:04–21:44:45 Codex A: `Reconnecting… 2/5 … 5/5`, then `waiting for
  network` ×6, all `willRetry`; status stays `active`
- 21:45:36 Wi-Fi on → captive probe `online`, `recovery`, duration 509s
- 21:45:36 `apis_ready waited_secs=0` — no `apis_waiting`; both API hosts
  resolved at once
- 21:45:38 Claude A `resume` (`status=error`, `network_error=True`,
  `error_in_outage=True`, `all_three_agree`) → `resume_sent ok`, 2s after recovery
- 21:45:39 Discord `notify sent=true` ✅ (first post, no retry needed)
- 21:45:40 `other-project` thread: same three signals → `resume_sent ok`,
  `notify sent=true`
- 21:45:40 `recheck_armed` (every 60s until 21:50:40)
- 21:45:42 Claude A starts a new turn; 21:45:46 Codex A resumes streaming on its own
- 21:46:41, 21:47:44, 21:48:46, 21:49:48 `recheck` → `apis_ready`,
  `bb_threads_enumerated count=0`. Nothing dead again, nothing new woken.
- 21:48:35 Codex A `turn/completed`, essay done, `idle`
- 21:50:19 Claude A `turn/completed`, essay done, `idle`. Zero `provider/error`
  after the revive.
- 21:50:50 `recheck_done`: window closed, watcher back to plain probing
- Control stayed `idle`. Zero false positives.

## Result

The bb loop is live-proven (ADR 0034): cut → death → gate → revive →
recheck → completion, unattended. The revived thread ran to the end with no
errors. Both Discord pings landed (0009's ping did not).

## The second revive

`other-thread-title` is not a 0010 thread. It was
real work in `other-project`, mid-turn when the cut hit. It died the same way
(10/10 retries, ENOTFOUND at 21:41:02, inside the window), so all three
signals agreed. Correct by the rules: the watcher covers every non-archived
`claude-code`/`codex` thread in bb, in every project. It got "keep going"
without David asking and resumed its task. Not a bug, but a real side effect
to know about.

## Not exercised

The 0009 guards ran but never had to do anything:

- `ready.py` DNS wait: `apis_ready` with `waited_secs=0` every time. No stale
  negative entry this cut. The wait branch did fire once today, in an earlier
  10.7-min cut (16:07–16:18 UTC) with nothing to revive: `apis_waiting` for
  `api.anthropic.com`, then `apis_ready waited_secs=71`. So the wait works
  live, but no revive has yet ridden through it.
- Discord retry: first post succeeded.
- Re-revive (`MAX_REVIVES`): nothing died again.

## Learnings

- "keep going" made Claude A rewrite the essay from the title. Claude Code
  replays the turn from the top rather than continuing mid-stream. Fine for a
  prototype; wasteful on real work.
- The `main` acp-cursor thread that drove the experiment lost its tool bridge
  in the cut (every command "service unavailable"). Its status stayed `idle`,
  and `acp-cursor` is outside the watcher's providers, so nothing acted on it.
  It came back on its own when David messaged it at 21:46:50. Cursor threads
  in bb are an uncovered host.
- Codex CLI's own retry loop survived 8.5 min again; Claude Code's died after
  ~3 min. Same as 0003/0004/0009.
- Scope is machine-wide by design. Any dead Claude/Codex thread in bb gets
  revived, not just the ones you are watching.

## Still not proven

A real cut where the stale DNS entry recurs with a dead agent, showing
`apis_waiting` → `apis_ready` → `resume_sent` → the agent stays alive.
