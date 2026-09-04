# Experiment 0007 — bb revive live (`--mode auto`) and Codex-in-bb death mode

Date: 2026-09-02 00:40 local (22:40Z). First cut under ADR 0037: radio +
Tailscale only, netguard verified loaded, independent restore timer armed
first, everything launched from bb terminals (not launchd). Machine never at
risk: the script restored itself, timer and netguard had nothing to do.

## Setup (bb project offline-agent-restart, all chat-only)

- `thr_tumwpigjd7` — Claude Code, 12-part essay, mid-task → expect `resume`
- `thr_6fx6nki7iu` — Codex, same essay, mid-task → learn death mode
- `thr_d39jwnheac` — Claude Code, one sentence, finished (`idle`) → guard
- `thr_2y9crng5ku` — Claude Code, waiting on `AskUserQuestion` (status `active`, pending interaction) → guard, must never be touched
- Watcher daemon pid 743 (post-reboot). Capture loop every 60s → `0007-captures/`.

## Timeline (UTC)

- 22:38:29 outage script aborts: "no live restore timer" (guard works)
- 22:38:50 restore timer armed in bb terminal (fires 22:52:52); capture loop started
- 22:40:09 cut: `tailscale down`, airport off. Probe 000 at 22:40:13.
- 22:40:15 watcher `to_offline`
- 22:44:22 Claude thread dies: `API Error: Can't reach the API server … (ENOTFOUND)`, turn `failed`, status `error` (after 10 retries, ~4 min)
- 22:41–22:52 Codex thread logs `Reconnecting... waiting for network` every ~63s, `willRetry: true`, status stays `active`
- 22:52:15 window complete; script restores. Airport on, probe 200 at 22:52:22.
- 22:52:30 watcher `offline_to_online`, duration 735s → `recovery`
- 22:52:31 Claude thread: `resume` (all three signals) → `resume_sent ok=false`, `HTTP 409 Thread is not active`
- 22:57:10 Codex thread finishes its turn on its own: `turn/completed status=completed`, status `idle`
- 22:57:50 agent sends the fixed command (`host_bb.resume`, `--mode auto`) by hand → `Thread updated`, status `active`, essay streaming again by 22:59

## Decisions (watcher, unattended)

- Claude essay → `resume` ✅ (revive delivery failed, see below)
- Codex essay → not listed (status `active`, never `error`) ✅
- Finished Claude → not listed (`idle`) ✅
- Question thread → not listed (`active` with pending interaction) ✅
- 5 older `error` threads → `skip` (`error_not_network`, `timing_miss`, `no_provider_error`) ✅
- False positives: 0. Guards never touched.

## Findings

1. **The 409 was stale code, not a wrong fix.** The daemon (pid 743) started
   at 23:52 local after the reboot; `--mode auto` landed in `host_bb.py` at
   00:09. Nobody restarted the daemon. The fixed command was then proven by
   hand on the same dead thread: `error → active`, essay continued. Daemon
   restarted (pid 13366). **Rule: restart the daemon after every code change,
   and log the exact command sent** (added to the checklist below).
2. **Codex inside bb survived 8 and 12 minute cuts** (0006, 0007). It
   reconnected every ~63s and completed the turn 4m40s after recovery. No
   revive needed, none sent. This differs from Codex CLI in cmux (experiment
   0004: dies at ~5 min). Whether it dies on longer outages (30–60 min) is
   unknown — do not assume "never". Its reconnect events carry
   `willRetry: true`, so the watcher ignores them until a terminal error
   appears; if one ever does, its text must be checked against the
   fingerprints.
3. **Claude in bb dies at ~4 min, not ~70s.** Ten API retries with growing
   backoff before the terminal `ENOTFOUND`. Cuts shorter than ~5 min will not
   kill a bb Claude thread. (cmux Claude Code: ~70s, experiment 0001.)
4. **A thread waiting on `AskUserQuestion` stays `active` in bb** and produces
   no error during the outage. The `status == error` filter alone protects it.
5. **ADR 0037 mechanics work.** Precondition guard aborted a run without a
   timer; the cut ran from a bb terminal and restored itself; the marker files
   were cleaned up; netguard logged nothing. David kept working.

## Status

- bb detection: proven live (0006, 0007).
- bb revive: command proven live by hand (0007); unattended delivery by the
  daemon still unproven — one more cut with the restarted daemon, or a
  simulated `error` thread if bb ever passes proxy env vars.
- Codex in bb: survives ≤12 min cuts on its own; longer outages untested.
  Detection correctly stayed silent while it was retrying.

## Checklist for the next cut

1. `git diff` clean, daemon restarted after the last code change, `alive` pid in log newer than `host_bb.py` mtime
2. `launchctl list | grep netguard`
3. `bb terminal create … restore-timer.sh <outage+2>`
4. `bb terminal create … 0001-run-outage.sh` with `OUTAGE_SECS` ≥ 360 for a bb Claude thread
5. Announce start/end to David
