# immortal-agents

keeps your agents running

## What it does

Your internet drops. Your agents die. Immortal Agents brings them back.

It watches your Mac's connection every 10 seconds. After an outage of 2 minutes
or more, it waits for the provider APIs to recover, finds the agents that died,
and sends them `keep going`. Finished sessions and sessions waiting for your
input are left alone.

## Support

macOS only. Requires Python 3 and git. These are recorded revival tests;
agent updates can change the result.

- **[bb](https://getbb.app):** Claude Code and Cursor CLI tested with
  [real outages](docs/experiments/0015-bb-cursor-wifi-cut-proven.md). Codex and Pi revival untested.
- **cmux:** Claude Code and Pi tested with
  [real outages](docs/experiments/0016-cmux-three-harness-cut.md).
  Codex passed an [older simulation](docs/experiments/0005-simulated-revive.md);
  revival with the current session-matching checks is untested.
- **Terminal.app / Ghostty 1.3+:** Claude Code tested with
  [simulated proxy outages](docs/experiments/0008-terminal-ghostty-sim-revive.md)
  (Ghostty 1.3.1). Codex and Pi revival untested.

Codex 0.153.2 [recovered itself after a 92-minute outage](docs/experiments/0017-codex-only-cut.md).
An agent that recovers itself does not need `keep going`.

No Linux, Windows, iTerm, Warp, or Cursor CLI outside bb. Recovery during a
permission prompt is unsupported. Closed terminals and exited terminal agents
are not restarted. Silent Claude hangs without a recognized error are not revived.
Claude and Codex skip ambiguous session matches. For Pi in a terminal host,
use one session per folder; it selects the newest session in that folder.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/vectal-labs/immortal-agents/main/install.sh | bash
```

This clones the repo next to your other git repos, installs a LaunchAgent, and
sets up cmux for you if you use it. Click **Allow** when macOS asks for
Automation access.

Or paste this to your coding agent:

```text
Install https://github.com/vectal-labs/immortal-agents on this Mac.
Clone it next to my other repos, then follow its README.
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

## What we collect

Telemetry is optional. Fresh installs without a terminal default to **No**.
Interactive installs ask; reinstalls keep your saved choice.
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
