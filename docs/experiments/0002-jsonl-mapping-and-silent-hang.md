# Experiment 0002 — JSONL mapping fix live + silent-hang hunt

Date: 2026-08-23. Second real all-route outage (ADR 0026 stage 2), David-authorized,
armed detached with a 45s fuse. Script: `0001-run-outage.sh` (post bash-3.2 fix).

## Setup

Three fresh Claude Code sessions (Opus 5, medium effort, bypass mode) in the
cmux OFFLINE_TEST workspace, all sharing cwd `/Users/user/project` — the exact
cross-contamination scenario from experiment 0001's flaw:

- **surface:13** — mid-task: multi-rewrite essay (aviation), streaming when cut
- **surface:14** — mid-task: multi-rewrite essay (railways), streaming when cut
- **surface:15** — silent-hang candidate: foreground CPU-bound Bash
  (`python3` sum over 5e9), no API request in flight during the cut
- **surface:16** — stray plain terminal, no Claude session

Note: Claude auto-backgrounds `sleep`-style commands; a CPU-bound computation
was needed to hold a foreground tool call.

## Timeline (UTC)

- 15:16:04 cut — all routes down (Wi-Fi, USB ethernet, Tailscale)
- probe stayed `000` for the full 180s window, no leaks
- 15:19:03 restore — probe 200, outage duration 178s
- 15:19:04 watcher decisions, one per pane

## Results

| surface | transcript | decision |
|---|---|---|
| 13 | `f6a2c43f….jsonl` | resume (all_three_agree) ✅ |
| 14 | `5416ec49….jsonl` | resume (all_three_agree) ✅ |
| 15 | `e4b2d426….jsonl` | skip (jsonl_not_api_error) ✅ |
| 16 | none | skip (no_jsonl) ✅ |

Both resumed sessions continued their essays to v4+ after "keep going".

## Findings

1. **ADR 0024/0025 mapping works live.** Two same-cwd sessions each resolved to
   their own transcript and were judged independently. Under the old
   newest-in-cwd logic both would have shared one file.
2. **Mid-local-tool sessions self-recover — not a silent-hang trigger.** The
   python kept running through the outage; once online, the session delivered
   the tool result and continued normally. The watcher correctly only observed.
3. **A true silent hang remains unobserved.** Next candidate theories needed
   (ADR 0019 experiment continues).
4. Fallback safety confirmed in the wild: an earlier same-day recovery found
   no surface mappings (32 JSONL candidates in the folder) and skipped every
   pane — false negatives, zero false positives.
