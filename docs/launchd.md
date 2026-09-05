# LaunchAgent install

Do **not** load this plist until you want the watcher running forever. The files are provided only.

cmux Settings → Automation → socket control mode must be `automation`. `cmuxOnly` blocks launchd.

Edit `launchd/com.immortal-agents.watcher.plist` so `ProgramArguments` and `WorkingDirectory` point at this clone, and `WATCHER_STATE_DIR` / log paths are where you want them. The shipped plists use the placeholder home `/Users/operator`; replace it with your own (launchd does not expand `$HOME`). The Python path must be absolute (`/opt/homebrew/bin/python3` on Apple Silicon Homebrew).

```bash
mkdir -p ~/.immortal-agents
cp launchd/com.immortal-agents.watcher.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.immortal-agents.watcher.plist
```

Uninstall:

```bash
launchctl bootout gui/$(id -u)/com.immortal-agents.watcher
rm -f ~/Library/LaunchAgents/com.immortal-agents.watcher.plist
```

Logs: `~/.immortal-agents/watcher.log` (structured JSON, size-capped). launchd stdout/stderr: `stdout.log` / `stderr.log` in the same directory.

## netguard safety net (Incident 0001)

`launchd/netguard.sh` runs every 120s. It re-enables any disabled network service, and turns the Wi-Fi radio back on only when `~/.immortal-agents/cut-armed.json` (written by the outage script) has a passed deadline. It never turns anything off.

```bash
cp launchd/com.immortal-agents.netguard.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.immortal-agents.netguard.plist
```

Log: `~/.immortal-agents/netguard.log`. Dry run: `NETGUARD_DRY_RUN=1 bash launchd/netguard.sh`.

## Simulated outage

The launchd watcher and `sim.py` must use the same `WATCHER_STATE_DIR` (the provided plist uses `~/.immortal-agents`). The flag only overrides probe results; the Mac stays online and recovery uses the normal watcher path.

```bash
python3 sim.py on --minutes 3
python3 sim.py status
python3 sim.py off
```

Always run `off` when a test ends early. Otherwise, the flag removes itself at its `expires_at` time and the watcher logs `sim_expired` before resuming real probes.

## Label history

The labels used the owner's personal reverse-DNS prefix until the open-source scrub (`docs/open-source/01-secrets-scan.md`, finding 6). If an old label is still loaded, `launchctl bootout gui/$(id -u)/<old-label>` first, then bootstrap the `com.immortal-agents.*` plist.
