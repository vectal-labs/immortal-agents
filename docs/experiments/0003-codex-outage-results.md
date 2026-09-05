# Experiment 0003 — Results: Codex survives prolonged outages

Date: 2026-08-23. Real all-route cut, operator-authorized, armed detached.
Staging per 0003-codex-outage-plan.md: five Codex v0.149 panes (`gpt-5.6-sol
high fast`), shared cwd — three identical mid-task essay sessions (revival
arm), one finished, one waiting at an approval prompt.

## Timeline (UTC)

- 16:17:44 cut start; probe dead (`000`) at every 15s check for the full 180s
- 16:20:58 routes restored; connectivity took ~2 min more to actually return
- Watcher measured the outage as 297s offline (16:17:51 → 16:22:48)
- Post-restore network flapping: 7 short blips (10-82s) over the next 12 min,
  all correctly skipped as `outage_too_short`

## Result: nothing died

All three mid-task panes rode out the ~5-minute blackout and completed their
full 6-essay task (18 files in /tmp/claude/). No stuck-at-Working, no error
record in any rollout, no dead conversation. The finished and waiting panes
were unaffected. The three revival treatments (untouched / Esc+retype /
kill+resume) were moot — there was nothing to revive.

## Findings

1. **Codex v0.149 is outage-resilient at this scale.** Its retry/backoff
   quietly rode out ~5 minutes of total blackout mid-task and finished the
   work. Sharp contrast with Claude Code, which gives up after ~70s
   (experiment 0001).
2. **The GitHub stuck-forever reports did not reproduce** (openai/codex
   #17433/#17003/#649). Those involve older versions and network *changes*
   (new Wi-Fi, wake from sleep) rather than a clean blackout on the same
   route. Death mode may be scenario-specific, not outage-duration-specific.
3. **Watcher bug found: `unknown_harness` on every pane.** Harness detection
   never identified the Codex panes, so all six surfaces were skipped. Safe
   (zero false positives) but zero coverage. Root cause to fix: cmux's
   `codex-hook-sessions.json` has a different schema than Claude's — a
   `sessions` dict keyed by session id (no `activeSessionsBySurface`), because
   the `cx` alias bypasses cmux's command wrapper, so surfaces are never
   attributed.
4. ADR 0028 (revive mechanism) and ADR 0030 (silent-hang policy) get their
   evidence: at 3-5 minute outages there is nothing to act on. Both stay
   observe-only.

## Open questions

- Does Codex survive 10+ minute outages, or does a retry limit eventually
  kill it?
- Does the network-*change* scenario (Wi-Fi swap, sleep/wake) reproduce the
  reported websocket hang where blackout does not?
- Is Codex support needed at all for this tool's core promise?
