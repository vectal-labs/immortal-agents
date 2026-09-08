# Install (LaunchAgent)

One script, zero infrastructure (ADR 0040). Needs macOS, git, and `python3`.
bb revival also needs a Node the watcher can exec; see [bb and Node](#bb-and-node).

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
./install.sh status      # loaded/running + last probe + bb access; exit 1 if the watcher is not running
./install.sh check       # per-host Automation probe + bb access; exit 2 = skipped, 1 = denied or broken bb Node
./install.sh logs        # tail -f ~/.immortal-agents/watcher.log
./install.sh uninstall   # bootout + remove plist; keeps ~/.immortal-agents
```

After updating `main` in the primary checkout, run `./install.sh` there again,
then `./install.sh status`. This restarts the watcher with the updated code and
keeps its recovery state. Editing files alone does not reload a running Python
process. Test from the checkout with Python 3.11+:
`python3 -m unittest discover -s tests`; tests isolate watcher state from live files.

## Update alerts and releases

`./install.sh` also installs `com.immortal-agents.updates`: a separate user
LaunchAgent with `RunAtLoad` and a 3600-second interval, without `KeepAlive`.
It reads the public `release.json` while online. It never installs updates,
restarts agents, or depends on optional telemetry or Discord settings.

The checker compares stable numeric versions in `immortal/__init__.py`, verifies
that the matching public GitHub Release exists, and requests one Mac notification
per important release. A later minor release does not hide an older important fix.
Checks, pending updates, and notification reservations live in
`~/.immortal-agents/updates.json`, separately from recovery state. The checker and
updater share `updates.lock`; network failures leave the last known update visible.
Logs are `updates.stdout.log` and `updates.stderr.log` in that directory.

```bash
./install.sh status
./install.sh notification-test
./install.sh update
```

Installation requests a test notification from the actual LaunchAgent. A successful
AppleScript call means submitted, not visibly delivered. If no banner appears,
check System Settings > Notifications > Script Editor and Focus settings. Status
still shows pending updates. Notification failures are not repeatedly retried for
the same release; use `notification-test` to verify repaired settings.

`update` requires a clean, primary `main` checkout whose origin is the public repo.
It fetches the announced tag, verifies its commit and code version, and only
fast-forwards. It never stashes, resets, merges local work, or changes remotes.
It preserves the watcher plist, Node configuration, webhook, telemetry preference,
and recovery state. Only the watcher is restarted; success requires a new PID and
its startup log. If restart fails, the updated code stays in place and status tells
you to retry `./install.sh update`. Private/development checkouts use manual Git
updates followed by `./install.sh` instead. The managed-Codex updater runs the
new release's repeatable component migration before restarting the watcher and
retries failed migrations even when the repo version is already current. Existing
users of the old updater still need the one-time bootstrap/reinstall described in
[managed Codex recovery](codex-recovery.md). No stable Codex component has been
published yet. Plist migrations still require explicit reinstall instructions.

### Publishing an important update

1. Approve the update-notification exception to ADR 0012. Do not rewrite that ADR.
2. Bump `__version__`, run the full test suite, and verify on another Mac. Review
   the public export; never copy private history, logs, or credentials into it.
3. Publish the tested public commit with tag `v<version>` and a non-draft,
   non-prerelease GitHub Release. The tag's code version must match.
4. Only after that release exists, update the public `release.json` on `main`.
   Keep older important entries so clients that missed a check still hear about fixes.
   Each entry needs these fields (replace every placeholder):

```json
{
  "schema": 1,
  "releases": [{
    "version": "0.1.0",
    "published_at": "2026-09-05",
    "important": true,
    "summary": "One sentence explaining why users should update.",
    "commit": "<full 40-character public release commit SHA>",
    "notes_url": "https://github.com/vectal-labs/immortal-agents/releases/tag/v0.1.0"
  }]
}
```

5. Run `python3 -m immortal.core.updates announcement`. It validates the local
   feed and public release and prints a shared Discord announcement; it sends nothing.
   Post it to the agreed project channel. Ask users to select GitHub **Watch >
   Custom > Releases** for release emails as a backup.
6. Existing installations need one manual bootstrap from their public clone:
   `git pull --ff-only && ./install.sh`. Old code cannot receive the new alerts
   until this step. Rerunning the piped installer alone does not pull an existing clone.

The checked-in feed starts empty intentionally: no version is advertised until
its tested public release exists. Test checker failures and updates with isolated
`WATCHER_STATE_DIR` directories, local Git repositories, and mocked host commands.
Never restart the live watcher or publish an announcement as part of unit tests.

## bb and Node

The bb CLI starts with `#!/usr/bin/env node`. launchd's PATH is
`/usr/bin:/bin:/usr/sbin:/sbin`, so a Node that only exists in a manager
(nvm, fnm, Volta, asdf, mise) is invisible to the watcher.

`./install.sh` finds Node and bb in the installer's environment, resolves
manager shims and temporary paths (`process.execPath`), keeps a Homebrew
keg (for example `node@22`) instead of swapping to a different default
Node, and saves the pair to `~/.immortal-agents/bb_runtime.json` plus
`IMMORTAL_NODE` / `BB_BIN` in the plist. Every bb call uses `[node, bb, …]`
with the LaunchAgent environment (default launchd PATH, no installer
`BB_CLI` / `NODE_OPTIONS`). `status` and `check` probe only; they do not
rewrite the LaunchAgent.

A running watcher is not the same as working bb access:

- watcher running, bb reachable: both lines are fine
- watcher running, bb not installed: bb is skipped; Terminal / Ghostty / cmux still work
- watcher running, Node missing or stale: bb line tells you to install Node 22+ and run `./install.sh`
- watcher running, bb app closed: bb line says to open bb, then `./install.sh check`.
  Install will not say "All set" or call a Node problem a permission denial.

Repair: install a current Node, open bb, run `./install.sh` again. After a
Node upgrade that removes the saved binary, the watcher rediscovers a
compatible runtime or prints that same repair line.

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
