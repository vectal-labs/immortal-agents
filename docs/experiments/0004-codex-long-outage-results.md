# Experiment 0004 — Results: Codex dies at ~5 min, "keep going" revives it

Date: 2026-08-26/27. Real all-route cut of 900s (`OUTAGE_SECS=900`),
operator-authorized, agent-armed. Three identical fresh Codex v0.149 sessions
(`gpt-5.6-sol high fast`, cwd ~/code) mid-task on a 12-rewrite essay job. An
offline-safe snapshotter (`0004-capture.sh`) recorded pane text, rollout tails,
and a probe every 60s; synthetic stand-ins for the captures are in `0004-captures/`.

## Timeline (UTC, 2026-08-26)

- 20:13:21 tasks fired; all three "Working"
- 20:15:09 cut start; probe `000` for the full 900s
- 20:19:25 (t=300s) still "Working" in all panes
- 20:19:57 all three die within one second of each other (rollout writes
  `token_count` then `event_msg/task_complete`)
- 20:20:25 (t=360s) snapshots show the error in all panes
- 20:30:25 restore, probe 200. Panes unchanged for 10+ hours afterwards.

## How Codex dies

**Loud, and it gives up after ~4m50s** (20:15:09 → 20:19:57). Pane shows:

```
■ unexpected status 502 Bad Gateway: Provider unreachable: Unable to connect.
Is the computer able to access the url?, url: http://127.0.0.1:10100/v1/responses
› Ask Codex to do anything
```

then returns to the idle prompt. Notes:

- The error text comes from the local proxy in `~/.codex/config.toml`
  (`openai_base_url = http://127.0.0.1:10100/v1`). A direct-to-OpenAI setup
  will word it differently ("stream error: exceeded retry limit" family).
- **The rollout JSONL records no error** — the dead turn ends with
  `task_complete`, identical to a clean finish. JSONL alone cannot tell a
  dead Codex from a finished one; the pane text is the distinguishing signal.
- No essay files were written: death came before v1 finished.

## Revival treatments (2026-08-27 06:44Z, ~10h after restore)

- **Pane 1, untouched (control):** still dead at the prompt. No self-recovery.
- **Pane 2, typed "keep going" + Enter:** revived immediately — "I'm continuing
  from the empty target directory…", Working. ✅
- **Pane 3, `/quit`, `codex resume <id>`, "keep going":** also revived, after
  dismissing an update prompt. Works, but strictly more steps for the same
  result. (`/quit` prints the session id: "To continue this session, run
  codex resume <id>".)

## Findings

1. **Codex's patience is ~5 minutes** (experiment 0003 survived a 297s outage;
   this one died at ~290s into a 900s cut). Below that it self-recovers; above
   it, it is dead until someone types.
2. **Revival = type "keep going" + Enter.** Same delivery as Claude Code
   (ADR 0010); no ESC needed (already idle), no kill, no `codex resume`.
3. **Detection needs the pane.** Fingerprints: `unexpected status 502`,
   `Provider unreachable`, `Unable to connect`, `Is the computer able to access
   the url` (proxy setup), plus the upstream "stream error" family. JSONL
   `task_complete` is NOT a finished-clean signal for Codex on its own.
4. Watcher still skipped every pane as `unknown_harness` (bug from 0003
   remains; Codex surface mapping unresolved).
5. Bonus: natural outages overnight (33 min, 13 min) were logged and skipped
   safely.

## Decisions this unblocks

- ADR 0028 (revive mechanism): "keep going" via cmux send — evidence in hand.
- ADR 0030 (silent hang): none observed; Codex dies loudly. Threshold for
  acting on Codex: outage ≥ ~5 min AND pane shows the death fingerprint.
