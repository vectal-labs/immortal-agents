# 0001 — Wi-Fi kill on Claude Code CLI (cmux)

Date: 2026-08-21. Agent: Claude Code CLI 2.1.237 in cmux workspace `wifi-kill-exp` (`surface:12`). Session `sess-0001-claude`. JSONL: `~/.claude/projects/-private-tmp-offline-agent-restart-scratch-wifi-kill-exp/sess-0001-claude.jsonl`. Synthetic fixtures (raw captures removed before open-sourcing): [0001-captures/](0001-captures/).

## Setup

Task: implement `maze_solver` (generator, A*/BFS, CLI, 25+ tests) in `/tmp/offline-agent-restart-scratch/wifi-kill-exp`. Mid-turn state at cut: streaming (`Catapulting… thinking with xhigh effort`) after one local bash tool call.

This Mac's default route was a USB ethernet adapter, and `en0` Wi-Fi also had internet. `networksetup -setairportpower en0 off` alone would not drop the API. The outage disabled Wi-Fi plus every USB ethernet service, then re-enabled all of them. Probe during outage: `curl` to `http://captive.apple.com/hotspot-detect.html` failed (`http_code=000`).

cmux was not running at the start of this work and its socket is `cmuxOnly` by default, so a LaunchAgent cannot talk to panes until Settings → Automation → socket control mode is `automation`.

## Timeline (UTC)

- `15:58:22Z` — user prompt recorded in JSONL.
- `15:59:21Z`–`15:59:22Z` — assistant `tool_use` + `tool_result` (local `ls`).
- `15:59:26Z` — network cut. Pane still mid-stream (`Catapulting… ↓ 1.3k tokens`).
- `15:59:27Z` — immediate snapshot: still streaming (`thought for 1s`).
- `15:59:58Z` (~31s) — pane: `Waiting for API response · will retry in 2m 29s · check your network`. JSONL unchanged (24 lines). Process still alive.
- `16:00:28Z` (~62s) — same retry UI, countdown `1m 59s`. JSONL still 24 lines.
- `16:00:36Z` (~70s) — JSONL gains assistant `{isApiErrorMessage: true, error: "server_error"}` text `API Error: Connection lost mid-response. The response above may be incomplete.` then `system.turn_duration` (134249 ms). 28 lines.
- `16:00:58Z` onward — pane shows that API error and sits at the idle `❯` prompt. Process did not exit.
- `16:01:59Z` — network restored. Claude did **not** auto-resume.

## What the terminal showed

1. Mid-stream keep-alive, then a visible retry with `check your network`.
2. After the retry window (~70s here), a hard `API Error: Connection lost mid-response` and return to the prompt.
3. Not a silent hang. Not a process crash.

## JSONL tail shape (after settle)

Last meaningful records are **not** an open `tool_use`. They are:

- assistant with `isApiErrorMessage: true`, `error: "server_error"`, `stop_reason: "stop_sequence"`
- `system` / `subtype: "turn_duration"`

`turn_duration` also appears after a *clean* finish, so it is not enough by itself. The API-error assistant record is the JSONL fingerprint of this outage.

## ESC + "keep going" + Enter (ADR 0010)

At `18:00:28Z` (session still idle on the API error): `cmux send-key esc`, then `cmux send "keep going"`, then Enter.

Result: session accepted the prompt and started writing files (`Write(maze_solver/__init__.py)` within 12s). ESC was harmless on the idle-with-error prompt.

## Detection implications

All three ADR 0008 signals, after a prolonged outage:

1. **Timing** — last real work (user / `tool_use` / `tool_result`) sits at or just before the drop; the API-error record is stamped during the outage.
2. **JSONL** — tail has `isApiErrorMessage` / `API Error: Connection lost`. Do **not** require a dangling `tool_use`. Do **not** treat `turn_duration` as "finished cleanly".
3. **Pane** — `Waiting for API response` + `check your network`, or `API Error: Connection lost mid-response`.

Skip if the pane/JSONL show a normal completed turn (no API error) or a wait-for-user UI.

## ADR notes

- ADR 0004: this experiment ran before the watcher was written.
- ADR 0009: HTTP probe to Apple's captive-portal URL is the right ground truth; interface-up is not.
- No contradiction. Practical extras: this machine needs ethernet *and* Wi-Fi cut; cmux must be in `automation` mode for launchd.
