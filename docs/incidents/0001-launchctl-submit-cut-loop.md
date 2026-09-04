# Incident 0001 — Agent-launched outage script looped and left the Mac offline

Date: 2026-09-01, 23:35–00:00 local (21:35–22:00 UTC). Severity: high.
David's MacBook lost all internet for ~25 minutes, Wi-Fi was greyed out and
unclickable, a reboot did not fix it, and he had to debug from his phone.

## Summary

An agent (this thread) ran the experiment outage script through
`launchctl submit`. That launcher restarts jobs when they exit, so the script
cut the internet a second time 20 seconds after restoring it. When David
rebooted mid-cut, the script was killed before it could re-enable the network
services it had disabled. Those disables are persistent macOS settings, so
they survived the reboot. Wi-Fi stayed greyed out until David re-enabled the
service by hand.

## Timeline (UTC, from `docs/experiments/outage-runs/0006-launchd.log` and the watcher log)

- 21:34:51 Agent arms the cut: `launchctl submit -l com.davidondrej.outage0006 -- bash -c 'sleep 20; OUTAGE_SECS=480 0001-run-outage.sh'`
- 21:35:11 Run 1 starts. Tailscale down, airport off, services disabled: `AX88179A/B`, `USB 10/100/1000 LAN`, `iPhone USB`, `Wi-Fi`, `Tailscale`.
- 21:43:20 Run 1 window complete. Restore succeeds: all services Enabled, airport On, probe 200. **The test itself worked.**
- 21:43:32 Script exits 0.
- 21:43:52 **launchd restarts the job.** Run 2 cuts everything again.
- 21:47:38 The bb wake-up automation fires and queues a message into this (dead) thread.
- 21:51:46 SIGTERM (David logging out / rebooting). Trap runs restore. Every `networksetup -setnetworkserviceenabled ... on` fails: `AuthorizationCreate() failed: -60008`, `Command requires admin privileges`. Tailscale CLI: `Failed to load preferences`. Restore ends with `probe=000`.
- ~21:52–22:00 Reboot. The submitted launchd job is gone (not persisted), but the six network services are still Disabled. Wi-Fi greyed out.
- 22:00:33 David re-enables Wi-Fi manually. Watcher sees online.
- 22:02 David messages the agent. Agent finds `AX88179A/B`, `USB 10/100/1000 LAN`, `iPhone USB`, `Tailscale` still Disabled and Tailscale backend Stopped; re-enables all, `tailscale up`. Probe 200.

## Root causes

### 1. `launchctl submit` keeps the job alive

`man launchctl`: "submit … A simple way of submitting a program to run without
a configuration file. This mechanism also tells launchd to keep the program
alive in the event of failure." In practice it behaves as `KeepAlive: true`
and relaunches after a clean exit too — confirmed by the 20-second gap between
"RESTORE done" and the next "OUTAGE script start". It was chosen because a
plain `nohup … &` from the previous attempt had been killed when the agent's
own process ended (the agent is a bb thread and dies with the internet).

### 2. `networksetup -setnetworkserviceenabled <service> off` is persistent

This is a configuration change written to
`/Library/Preferences/SystemConfiguration/preferences.plist`, not a runtime
toggle. It survives reboots. A disabled Wi-Fi service removes the Wi-Fi toggle
from the menu bar and System Settings — the "greyed out, cannot click" symptom.
Recovery is System Settings → Network → ⋯ → **Make Service Active**, or
`networksetup -setnetworkserviceenabled Wi-Fi on`.

By contrast `networksetup -setairportpower en0 off` only turns the radio off;
the toggle stays clickable and the user can always flip it back.

### 3. Restore ran without authorization

`networksetup` needs admin rights. Run 1's restore succeeded because the
agent's process inherited David's logged-in GUI session authorization. Run 2's
restore ran during logout/reboot, when the Authorization server was already
refusing requests (`-60008`), so every re-enable failed. The script logged the
failures and exited; nothing retried later.

### 4. Process failure: ADR 0020 was overridden

ADR 0020 ("David launches test outages himself") exists because an unattended
cut can strand the machine. David gave explicit permission for the agent to run
it this time; the agent should still have refused to launch it from a
mechanism it had not verified, and should have kept the disable/restore pair
inside one process that cannot be killed by the test itself.

## What worked

- The outage script's leak check, window, and restore logic were correct; run 1 restored cleanly.
- The watcher daemon (launchd, KeepAlive intended) survived everything and logged all three recoveries.
- The bb host detection fired correctly on the Claude thread (see experiment 0006) with zero false positives.

## Fixes

Done in this incident:

- Re-enabled all six network services and Tailscale (manual, by the agent, after David restored Wi-Fi).
- Confirmed no `outage0006` job remains loaded; deleted the wake-up automation.
- Fixed the unrelated revive bug found by the test (`bb thread tell … --mode auto`).

Done 2026-09-02 (commit after this incident):

1. **Outage script no longer disables services.** `0001-run-outage.sh` now cuts only Wi-Fi radio power and Tailscale — both reversible toggles. Wired adapters must be unplugged; the leak check aborts otherwise.
2. **Script refuses to run detached.** It aborts unless stdin/stdout are a terminal and the parent is not launchd. `launchctl submit`, nohup, or an agent cannot run it. ADR 0020 is now enforced in code.
3. **netguard watchdog installed.** `ops/launchd/netguard.sh` (LaunchAgent, every 120s) re-enables any disabled network service, and turns Wi-Fi radio on when the script's `cut-armed.json` deadline has passed. It never turns anything off. See `docs/launchd.md`.
4. **AGENTS.md rule added**: no cuts by agents, no `launchctl submit`, no `-setnetworkserviceenabled off`.

## Lessons

- A launcher that survives the agent is not the same as a launcher that runs once. Verify restart semantics before using any launchd path for a one-shot.
- Prefer reversible, non-persistent knobs (radio power, firewall rules, routes) over configuration changes for anything a test toggles.
- Any test that removes the agent's own ability to observe or intervene needs an independent fail-safe on the machine.
- "Full permission" from the user is not verification. The agent still owns the blast radius.
