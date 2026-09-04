# Experiment 0008 — Terminal.app + Ghostty revived via simulated outage

Date: 2026-09-02. First live proof of the two AppleScript hosts (`host_terminal.py`,
`host_ghostty.py`). ADR 0035 loop, no real internet cut: Claude Code in each app
pointed at the fake-outage proxy (`sim/proxy.py` on 10198, drop mode);
`sim.py on --minutes 5` flipped it dead and told the watcher it was offline.
David's Mac stayed online. Watcher ran unattended under launchd.

## Setup

- Terminal.app: `ANTHROPIC_BASE_URL=http://127.0.0.1:10198 claude '<long essay>'`
  in `/tmp/oar-exp8-terminal`. Trust dialog accepted via `do script "\e[B"`
  (Down + Return in one call).
- Ghostty 1.3.1: same in `/tmp/oar-exp8-ghostty`, window created and driven
  over JXA (`new window`, `input text`, `send key "arrowDown"`, `send key "enter"`).
- Both sessions were mid-request (5+ min of xhigh thinking on a live stream)
  when the cut started. Claude Code set both titles to
  "◐ Undersea telegraph cables 1850–1900" — no "claude" in the title.
- Controls: Terminal's default tab (shell), two unrelated Ghostty terminals.

## Timeline (UTC)

- 11:27:29 `sim.py on --minutes 5`; watcher `to_offline` 11:27:36
- ~11:28 both sessions: `Connection dropped (ECONNRESET) · Retrying … attempt N/10`
- 11:30:23 Ghostty Claude records `isApiErrorMessage`; 11:30:35 Terminal Claude
- 11:32:38 flag expired, real probe online → `recovery`, duration 302s
- 11:32:42 Terminal.app: `resume_sent ok`, Discord `notify sent`
- 11:32:44 Ghostty: `resume_sent ok`, Discord `notify sent`
- 11:32:42 / 11:32:44 both JSONLs show the user message "keep going"; both
  sessions resumed thinking

## Decisions (all unattended)

- **Terminal.app ttys004 (Claude, dead):** harness from `pane_text`; JSONL
  api_error + timing + pane `network_error` → `resume`. ✅ three signals
- **Ghostty (Claude, dead):** harness from `process` (claude pid under Ghostty
  with the same resolved cwd); pane `unreadable`; JSONL api_error + timing →
  `resume` with `two_signals_agree`. ✅ two signals
- Terminal default tab, Ghostty `ollama` and `~` terminals: `skip`,
  `unknown_harness`. ✅
- `resume_sent`: 2. False positives: 0. TCC/Automation errors under launchd: 0.

## Findings

1. **Both AppleScript hosts revive unattended from launchd.** No Automation
   prompt blocked `osascript` run by the daemon's `python3`.
2. **Bug found pre-cut, fixed:** Ghostty reports cwd `/tmp/x`, `lsof` reports
   `/private/tmp/x`; the harness hint was `null`. `host_ghostty` now compares
   `os.path.realpath` on both sides. Without the fix Ghostty would have been
   skipped as `unknown_harness` because Claude overwrites the title.
3. **Title is not a reliable harness signal.** Claude Code renames the tab to
   the task. The process→cwd hint is what carried Ghostty.
4. **Terminal.app `do script` delivers text cleanly without ESC.** The input
   box showed a status line ("Decompression error: ZlibError") but "keep going"
   arrived as its own message.
5. **Ghostty key names:** `arrowDown` works; `down` / `arrow_down` / `Down` are
   rejected. `enter` and `escape` are fine.
6. Staging: a background `&` proxy from an agent shell dies when the shell
   returns; run it in a persistent terminal (`bb terminal create`).
