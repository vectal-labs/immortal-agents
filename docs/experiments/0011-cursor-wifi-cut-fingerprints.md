# Experiment 0011 — Cursor CLI death fingerprints after a real Wi-Fi cut

Date: 2026-09-02. First live cut aimed at Cursor CLI, on three hosts at once
(cmux, Terminal.app, bb ACP). The operator flipped Wi-Fi off by hand for 12.3 min.
Watcher ran unattended under launchd (0010 code). Human flip, so ADR 0037
does not apply. Goal was fingerprints, not an unattended Cursor revive.

## Setup

- Three Cursor CLI sessions, model Auto, mid-essay when the cut hit:
  cmux surface:12 (`/tmp/oar-exp11-cmux`), a Terminal.app tab
  (`/tmp/oar-exp11-terminal`), bb `thr_e11cursor` "0011 Cursor bb: undersea
  cables" (ACP session `sess-0011-acp`).
- Capture loop (`0011-capture.sh`) snapped pane text, bb events, store
  mtimes, hooks, and process count every 20s into `0011-captures/` (synthetic stand-ins kept).
- Control: cmux surface:1 (old Cursor "Trust this workspace" dialog). Ghostty
  not running.

## Timeline (UTC)

- 22:14:14 capture staged; all three generating
- 22:21:23 Wi-Fi off → watcher `to_offline`
- 22:21:48 bb Cursor: essay cut at 71,561 chars, ends
  `Error: RetriableError: [unavailable] PING timed out`. Turn `completed`.
  Queued follow-up fails instantly with `Error: RetriableError: [internal]`.
  Thread goes `idle`. No `provider/error`. Process stays up.
- 22:22:00 cmux/Terminal still alive, then
  `Reconnecting to agentn.global.api5.cursor.sh (attempt N, Xs)`
- 22:29:04 both TUIs at attempt 11 (~441s)
- 22:29:27 Terminal `stop` hook `status: error`; 22:29:37 cmux same
- 22:29:46 both panes: `Error: Connection failed. The connection failed 10
  times.` Failed prompt is sitting in the composer. Process still alive.
  `store.db-wal` mtime jumps here; it was frozen all through reconnect.
- 22:33:42 Wi-Fi on → `recovery`, duration 739s. `apis_ready waited_secs=0`
- 22:33:42–43 watcher: cmux surface:12 and the Terminal tab `skip`
  `unknown_harness`. bb Cursor not enumerated (`idle`, and `acp-cursor` is
  out of `host_bb.PROVIDERS`)
- 22:33:44 collateral: `thr_e11other1` ("getting clarity",
  `next-main-project`, claude-code) `resume_sent ok`, Discord `sent=true`
- 22:36:06 / 22:36:17 / 22:36:25 this agent manually revived the three
  Cursor sessions
- 22:38:52 `recheck_done`. Rechecks found nothing else dead.

## Result

Cursor dies on all three hosts. The watcher does not see any of them.
Manual "keep going" does wake the TUI sessions.

## How Cursor dies

**cmux / Terminal.app (TUI)**

- Pane while dying: `Reconnecting to agentn.global.api5.cursor.sh (attempt N, Xs)`
- Pane when dead: `Error: Connection failed` / `The connection failed 10 times.`
- Hook: `stop` with `status: error` at that moment (`~/.label-agent-stops/cursor-hooks.jsonl`)
- Process stays up (`cursor-agent … index.js`)
- Session store is a weak signal: no error text, only an mtime bump at death
- Dead pane did not match Claude's `shift+tab to cycle` marker, so the
  watcher correctly said `unknown_harness` rather than mislabeling it Claude

**bb (ACP)**

- Soft death. Status `idle`, never `error`. Zero `provider/error` events.
- Fingerprint is the agent message suffix
  `Error: RetriableError: [unavailable] PING timed out`
- Today's `host_bb` three-signal test cannot see this

## Manual revive (not the watcher)

- **cmux:** ESC does not clear the composer. Typing "keep going" + Enter
  submits, but hooks show it appended onto the restored failed prompt.
  Session resumed the Pacific essay.
- **Terminal.app:** `do script "keep going"` only types. A second
  `do script ""` sends Return. Then it continued.
- **bb:** `bb thread tell … "keep going"` starts a new turn. The Cursor
  agent ignored the essay and started reading this repo. Some tools then
  failed with `Service temporarily unavailable` for ~2 min and recovered.
  Cursor-in-bb treats "keep going" as a fresh coding turn.

## The second revive

Same side effect as 0010. `getting clarity` in `next-main-project` was
mid-turn, died `ENOTFOUND` at 22:25:28, all three bb signals agreed.
Watcher sent "keep going" at 22:33:44. Correct by the rules. It wrote
files in that repo. Idle by 22:35.

## Still not built

Cursor coverage. Needed from this cut:

- TUI harness: pane fingerprints above + `stop`/`error` hook + process hint
- Revive: type "keep going", then Enter as its own key. No ESC. Terminal.app
  needs an extra Return after `do script`
- bb ACP: detect `idle` + `PING timed out` / `RetriableError` inside the
  outage window, not `status: error`
- Consider adding `api2.cursor.sh` to `ready.py` (hypothesized, not measured
  with `dscacheutil` this cut)
