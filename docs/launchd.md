# Install (LaunchAgent)

One script, zero infrastructure (ADR 0040). Needs macOS, git, and `python3`.

```bash
curl -fsSL https://raw.githubusercontent.com/vectal-labs/immortal-agents/main/install.sh | bash
```

Or by hand:

```bash
git clone https://github.com/vectal-labs/immortal-agents.git
cd immortal-agents
./install.sh                      # or: ./install.sh --discord <webhook-url>
```

Piped mode: when `install.sh` runs with no `watcher.py` next to it, it scans
`~` (up to 3 levels, skipping hidden dirs and `~/Library`) for the folder that
holds the most git repos, clones `main` into `<that folder>/immortal-agents`
(`~/code` when none is found), reuses an existing clone there, then `exec`s the
clone's `install.sh` with the same flags. Only `install` works piped; the other
verbs need the clone path.

`install.sh` writes `~/Library/LaunchAgents/com.immortal-agents.watcher.plist`
(RunAtLoad, KeepAlive, absolute `python3` + `watcher.py`, `WorkingDirectory` =
this clone, `WATCHER_STATE_DIR=~/.immortal-agents`), boots out any older
watcher label, and bootstraps it into `gui/$(id -u)`. The watcher starts
immediately and survives reboots. It never uses `launchctl submit`
(Incident 0001).

Other verbs:

```bash
./install.sh status      # loaded/running + last probe; exit 1 if not running
./install.sh check       # per-host Automation permission probe; exit 2 = skipped, 1 = denied
./install.sh logs        # tail -f ~/.immortal-agents/watcher.log
./install.sh uninstall   # bootout + remove plist; keeps ~/.immortal-agents
```

Permissions: Terminal.app and Ghostty need macOS Automation permission once.
macOS only raises the prompt for a running app, so the install opens each
installed-but-closed host in the background (`open -g`) and then sends one
harmless AppleScript read per host, using the plist's `python3`, so the prompt
appears during install. `./install.sh check` never launches apps; it
prints granted / denied / not running per host and the System Settings path to
fix a denial. Output uses colors and ✓ / ! / ✗ marks on a terminal; `NO_COLOR=1`
or a pipe turns colors off. cmux needs socket control mode `automation`; the default
`cmuxOnly` blocks launchd. `./install.sh` pins it in `~/.config/cmux/cmux.json`
(file-managed, so the Settings UI and cmux migrations cannot flip it back), backs
up the old file, and runs `cmux reload-config`. Older cmux builds only pick the
new mode up after a full quit and reopen. `./install.sh check` reads the
effective mode and fails when it is not `automation`.

Logs: `~/.immortal-agents/watcher.log` (structured JSON, size-capped). launchd
stdout/stderr: `stdout.log` / `stderr.log` in the same directory.

## Repository layout

`install.sh` and `watcher.py` stay at the repository root. The watcher calls
the root `revive.py`; everything else lives in the `immortal/` package (`core/`, `detect/`, `hosts/`, `sim/`).

## Optional netguard safety net (Incident 0001)

Netguard is installed by hand; `install.sh` never touches it. It is intentionally
aggressive: every disabled network service is re-enabled. Use it only on a Mac
where disabled services are always unexpected.

`ops/launchd/netguard.sh` runs every 120s. It re-enables any disabled network
service, and turns the Wi-Fi radio back on only when
`~/.immortal-agents/cut-armed.json` (written by an outage script) has a passed
deadline. It never turns anything off.

```bash
mkdir -p ~/.immortal-agents/bin ~/Library/LaunchAgents
cp ops/launchd/netguard.sh ~/.immortal-agents/bin/netguard.sh
chmod 700 ~/.immortal-agents/bin/netguard.sh
cp ops/launchd/com.immortal-agents.netguard.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.immortal-agents.netguard.plist
```

Log: `~/.immortal-agents/netguard.log`. Dry run: `NETGUARD_DRY_RUN=1 bash ops/launchd/netguard.sh`.

`ops/launchd/restore-timer.sh <minutes>` is a separate one-shot fallback. Run it in
an independent terminal before an authorized real outage test. It turns Wi-Fi
and Tailscale on when the timer expires, then exits. It never turns them off.

## Simulated outage

The launchd watcher and `python3 -m immortal.sim` must use the same `WATCHER_STATE_DIR`
(`install.sh` uses `~/.immortal-agents`). The flag overrides probe results;
the CLI also interrupts configured local proxies. Only agents routed through
those proxies lose their API connection. The Mac stays online and recovery
uses the normal watcher path.

```bash
python3 -m immortal.sim on --minutes 3
python3 -m immortal.sim status
python3 -m immortal.sim off
```

Always run `off` when a test ends early. Otherwise, the flag removes itself at its `expires_at` time and the watcher logs `sim_expired` before resuming real probes.
