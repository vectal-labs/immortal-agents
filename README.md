# <img src="favicon.png" width="32" height="32" alt=""> immortal-agents

keeps your agents running

## What it does

Revives agents after internet outages of 2+ minutes. Once provider APIs recover,
it sends stalled agents `keep going`. Finished sessions and agents waiting for
input are left alone.

## Support

macOS only. Requires Python 3 and git.

Tested revival:

- **[bb](https://getbb.app):** Claude Code and Cursor CLI ([real outages](docs/experiments/0015-bb-cursor-wifi-cut-proven.md)).
- **cmux:** Claude Code and Pi ([real outages](docs/experiments/0016-cmux-three-harness-cut.md)).
- **Terminal.app / Ghostty 1.3+:** Claude Code ([simulated outages](docs/experiments/0008-terminal-ghostty-sim-revive.md), Ghostty 1.3.1).

Results may change with agent updates. Codex revival is unverified with current
checks; Pi revival is untested outside cmux.

Unsupported: Linux, Windows, iTerm, Warp, Cursor CLI outside bb, and recovery
at permission prompts. Closed terminals, exited agents, and silent Claude hangs
without a recognized error are not revived.

Claude and Codex skip ambiguous session matches. For Pi in terminals, use
1 session per folder; the newest session is selected.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/vectal-labs/immortal-agents/main/install.sh | bash
```

This clones the repo next to your other git repos, installs a LaunchAgent, and
sets up cmux for you if you use it. Click **Allow** when macOS asks for
Automation access.

Or paste this to your coding agent to install with [optional diagnostics](#what-we-collect):

```text
Install https://github.com/vectal-labs/immortal-agents on this Mac.
Clone it next to my other repos and read its README.
I want to enable the optional usage and crash diagnostics described in
"What we collect" to help improve recovery. Run ./install.sh --telemetry.
Then run ./install.sh status and confirm the watcher is running and telemetry is on.
Tell me if I need to click Allow on any macOS popup.
```

Later, from the clone:

```sh
./install.sh status
./install.sh check                      # re-run the Automation permission probe
./install.sh logs
./install.sh uninstall
./install.sh --discord "<webhook-url>"  # optional Discord alerts
```

Prove it works with a real outage: [docs/test-drive.md](docs/test-drive.md).
See [docs/launchd.md](docs/launchd.md) for install details, logs, and how to
[simulate an outage](docs/launchd.md#simulated-outage) while staying online.

## Updates

Important public releases trigger a Mac notification. `./install.sh status` also
shows pending updates. Run `./install.sh update` from your clean public clone to
install a published release and restart only the watcher. Nothing auto-updates.

Existing users need one manual `git pull --ff-only && ./install.sh` to enable alerts.
Use `./install.sh notification-test` to test them; macOS notification settings and
Focus can hide banners. For backup emails, select **Watch > Custom > Releases** on
GitHub. See [update alerts and release instructions](docs/launchd.md#update-alerts-and-releases).

## What we collect

Telemetry is optional. Enable it with `./install.sh --telemetry`.
Without a telemetry flag, fresh installs without a terminal default to **No**.
Interactive installs ask; reinstalls keep your saved choice unless you pass a telemetry flag.
Check the setting with `./install.sh status`.
Disable it with `./install.sh --no-telemetry` or `echo off > ~/.immortal-agents/telemetry`.

When enabled, the [client](immortal/core/telemetry.py) sends:

- Every event: `machine_id` (random, saved installation ID), `macos` and `python`
  (versions), `commit` (git revision), `event` (name), and `ts` (timestamp).
- `install`, daily `heartbeat`, and `outage_detected`: no extra fields.
- `revive_attempt`: `attempt_id` (random), `harness`, `host`, `result` (input sent),
  and `error` (`network_outage` or `provider_outage`).
- `revive_confirmed` / `revive_unconfirmed`: `attempt_id`, `harness`, `host`,
  `reason`, and `elapsed_secs`. Confirmation means new agent output, not a
  finished task. See [recovery outcomes](docs/recovery-outcomes.md).
- `exception`: `type`, `file`, and `line`. The location is inside Immortal Agents,
  relative to this repo, or null when unavailable.

No prompts, source code, session IDs, raw error messages, or tracebacks are sent.
The installation ID links events from the same install, so these reports are
not fully anonymous.

The [server](ops/telemetry/server.py) retains up to 20 MiB across 2 event files.
Old files are replaced as they fill; there is no fixed expiry date.
Application logs omit IP addresses and request details. Hosting is on Render,
whose [separate request logs can include IP addresses](https://render.com/docs/logging#http-request-logs)
and have [plan-dependent retention](https://render.com/docs/logging#retention-period).
We do not promise zero IP logging by the host.

## Contribute

Run `python3 -m unittest discover -s tests` before opening a pull request.
Architecture decisions, experiments, and incidents live in [`docs/`](docs/).
