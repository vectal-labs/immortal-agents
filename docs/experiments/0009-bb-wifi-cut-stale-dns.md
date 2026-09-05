# Experiment 0009 — bb revive after a real Wi-Fi cut; stale DNS kills it again

Date: 2026-09-02. Second real cut for the bb host (ADR 0036). The operator flipped
Wi-Fi off by hand for 9.7 min. Watcher ran unattended under launchd. No
netguard needed: a human flip, not an agent-driven cut (ADR 0037 does not apply).

## Setup

- bb project `offline agent restart`, three threads writing a long essay
  (undersea telegraph cables, mid-stream when the cut hit):
  `0009 Claude A` (claude-code), `0009 Codex A` (codex), and a `control`
  thread (idle, one sentence already done).
- Discord webhook configured. Ghostty open with two unrelated terminals
  (`ollama`, `~`). cmux and Terminal.app not running.

## Timeline (UTC)

- 12:13:22 Wi-Fi off → watcher `to_offline`
- 12:14:23 Claude A last text delta; then `API Error … ENOTFOUND`, status `error`
- 12:23:04 Wi-Fi on → captive probe `online`, `recovery`, duration 581s
- 12:23:05 Ghostty: both terminals `skip`, `unknown_harness` ✅
- 12:23:05 bb: Claude A `resume` (`status=error`, `network_error=True`,
  `error_in_outage=True`, `all_three_agree`) → `resume_sent ok` (2s after recovery)
- 12:23:06 Discord `notify sent=false` ❌
- 12:23:06–12:25:30 Claude A: `Claude Code API retry 1/10 … 10/10`, all fail
- 12:26:00 Claude A back to `error`: *"Can't reach the API server — check your
  internet or DNS (ENOTFOUND)"*. Watcher did not act again (once per outage).
- Codex A never died: it kept retrying on its own and stayed `active`.
  Control stayed `idle`. Zero false positives.

## Root cause

At 12:25, three minutes after reconnect, on the same Mac:

- `dig api.anthropic.com` → `160.79.104.10` (real DNS fine)
- `dscacheutil -q host -a name api.anthropic.com` → empty (system resolver)
- `curl https://api.anthropic.com` → exit 6, could not resolve host
- `curl https://discord.com` → 200

While offline, Claude hammered `api.anthropic.com` and mDNSResponder cached
the failures. The captive probe passed, the watcher revived, and Claude
walked straight into the stale entry. Discord failed at 12:23:06 the same way.
The entry was gone by 16:04; exact TTL unmeasured (order of minutes).

## What changed

- `ready.py`: before any revive, wait until `api.anthropic.com` and
  `api.openai.com` resolve via `getaddrinfo` (same path the agents use).
  Cap 10 min, then proceed anyway.
- `watcher.py`: after a revive, re-check the revived targets every 60s for
  5 min with the outage window stretched to now. Dead again → "keep going"
  again, `MAX_REVIVES = 3` per outage. The recheck never wakes new targets.
- `notify.py`: Discord post retries 3 times (2s, 5s, 15s).

## Learnings

- The watcher's own logic was right: detection, timing, and revive all fired
  within 2s. The failure was downstream: the Mac's resolver, not the internet.
- "Internet is back" must be defined per agent API, not per captive probe.
- One-shot revive is too fragile after a real outage. Bounded retry is cheap.
- Codex CLI's own retry loop survived a 10-min cut inside bb; Claude Code's
  did not (10 retries, ~3 min). Same pattern as experiments 0003/0004.

## Not proven yet

The fix above is unit-tested and running under launchd, but not live-proven
(ADR 0034). Next real cut should show: `apis_waiting` → `apis_ready` before
`resume_sent`, and a `recheck` pass in the log.
