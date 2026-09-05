# Experiment 0005 — First unattended revive with a simulated outage

Date: 2026-08-29. First run of the ADR 0035 loop: no real internet cut. Test
sessions pointed at the fake-outage proxies (`sim/proxy.py`: Codex on 10199
with 502 dead mode, Claude on 10198 with drop dead mode); `sim.py on --minutes 6`
flipped both proxies dead and told the watcher it was offline. The operator's Mac
stayed online throughout.

## Setup (cmux OFFLINE_TEST, shared cwd ~/code)

- surfaces 1-2: Codex via `codex -c 'openai_base_url="http://127.0.0.1:10199/v1"'`,
  long chat-only essay task
- surface 3: Codex via the proxy, short task finished before the outage
- surface 4: Claude via `ANTHROPIC_BASE_URL=http://127.0.0.1:10198 claude`,
  long chat-only essay task
- surface 5: leftover idle Codex session (uninvolved control)

## Timeline (UTC)

- 11:15:35 `sim.py on --minutes 6`; watcher `to_offline` at 11:15:37
- 11:21:23 the operator closed the lid — clamshell sleep until 11:34:22 (unplanned,
  and a great extra test)
- 11:34:26 watcher wakes, flag expired (`sim_expired`); 11:35:48 real probe
  online → `recovery`, measured duration 1211s

## Decisions (all unattended)

- **surface 2 (Codex, mid-task):** `resume` — pane fingerprint + task_complete
  inside the outage → "keep going" sent → essay continued. ✅
- **surface 4 (Claude, mid-task):** `resume` — `isApiErrorMessage` + pane error
  + timing → "keep going" sent → essay continued. ✅ First unattended Claude
  revive via simulation; the proxy's drop mode reproduced the real death.
- **surface 1 (Codex):** `skip`, pane=other. Staging flaw: Codex answered the
  prompt with "Reply 'start' and I'll begin" instead of working, so it was
  never mid-task. Correct skip.
- **surface 3 (Codex, finished):** `skip` — but via `unknown_harness`, not
  `pane_not_network_error`. Bug: the narrow pane truncated the prompt to
  "Ask Codex to do anythi", so the marker "ask codex to do anything" missed.
  Fixed: marker is now "ask codex". Replays and tests still pass.
- **surface 5 (idle Codex):** `skip`, pane=other. ✅
- `resume_sent` count: 2. False positives: 0. Internet lost by the operator: none.

## Findings

1. **The simulated outage reproduces both real deaths** — Codex's 502
   fingerprint and Claude's connection error — and the watcher revives them
   through its normal code path. Per ADR 0034/0035, Codex detect-and-resume is
   now proven live.
2. **The watcher survives sleep.** A lid close mid-outage just delayed
   recovery until wake; nothing broke.
3. **Pane markers must survive narrow panes** (fixed).
4. Staging note: tell Codex "start immediately, do not ask" — chat-only long
   tasks otherwise get a clarifying question instead of work.
5. Codex first tries a websocket (`ws://…/v1/responses`) against the proxy,
   fails, and falls back to HTTP. Works, but a websocket-aware proxy would be
   more faithful.
