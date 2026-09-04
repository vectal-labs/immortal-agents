# 0037 — Agents may cut the internet, never permanently, with two independent recoveries

## Context

Incident 0001: an agent launched the outage script via `launchctl submit`,
which restarted it in a loop, and the script disabled network services — a
persistent setting that survived a reboot and greyed out Wi-Fi. David was
offline for 25 minutes with no way to talk to the agent. ADR 0020 banned
agent-launched cuts entirely, but that blocks the fast test loop David wants.

## Decision

Agents may cut the internet for a test (David, 2026-09-02) only if all three
hold:

1. **Reversible knobs only.** Wi-Fi radio power (`-setairportpower`) and
   `tailscale down`. Never `networksetup -setnetworkserviceenabled … off`,
   never edits to network preferences, never anything that survives a reboot.
2. **netguard is verified running first.** `launchctl list | grep netguard`
   must show the every-2-minutes watchdog (`ops/launchd/netguard.sh`) loaded, and
   the cut must write `cut-armed.json` with a deadline so netguard restores
   after it.
3. **A one-time restore timer is armed before the cut**, in a process that
   does not depend on the agent surviving (a bb terminal or a one-shot launchd
   plist without KeepAlive — never `launchctl submit`). It waits X minutes
   (sized to the test, e.g. outage + 2) and then runs `networksetup
   -setairportpower en0 on` and `tailscale up`, once.

Cut length is sized to what is being tested (Claude dies at ~70s, Codex CLI
at ~5 min). David is told the exact start and end time before the cut.

## Consequences

- Supersedes ADR 0020 ("David launches himself"). ADR 0015's fresh-approval
  rule still applies to every session.
- Two independent recoveries (timer + netguard) cover the case where the
  cutting process dies, as it did in Incident 0001.
- `0001-run-outage.sh` currently refuses to run non-interactively; it must be
  updated to check preconditions 2 and 3 instead.
- AGENTS.md network rules follow this ADR.
