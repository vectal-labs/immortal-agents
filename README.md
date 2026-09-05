# immortal-agents

Helps restart agents if they stop because of loss of internet.

## The problem

The problem trying to solve is agents not running — different AI agents not running.

## Scope

We can start very minimal — we're going to start by focusing on Claude Code only, and we're going to build a simple script.

### What we are NOT solving: brief internet loss

These CLI coding agents have retry for brief internet loss. If you lose internet for like a couple seconds, all of these agents have guardrails against this — they do retry immediately after a few seconds, a couple of times. That's not what we're talking about.

We're solving losing internet for more than just a few minutes — a minute or longer. Basically a longer period than any of these agent harnesses retry. Let's say you are out of internet for 30 minutes or 60 minutes: the moment your computer regains internet connection, this process would detect "hey, this MacBook has internet now" and it would resume all agent sessions that previously stopped when you lost the internet — by simply sending them a short prompt saying "keep going".

## How it works

Anytime our computer loses internet, it would check: do we have any agents running, or is there any agentic sessions that did any work in the last 30 minutes for example — to see if there is any agents that stopped running due to internet issues.

### 1. Detection

The first key thing we need to solve — the first technical problem — is figure out how to correctly detect AI agents when they stop running due to internet issues specifically, and loss of internet specifically.

### 2. Restarting

The second one is like restarting it, which is pretty easy. The moment a computer regains internet, it would send a simple prompt "keep going" to all of the different coding agents that were stopped because of us losing the internet connection.

It needs to be very careful because it shouldn't send this to all AI agents. It should send it only to those that specifically stopped running in the middle of a task because the computer lost internet.

## Current shape

`watcher.py` (Python 3 stdlib) probes real internet every 10s. After an outage of 120s+ it reads every cmux pane, identifies the harness from the pane text itself (Claude Code footer vs. Codex prompt), and decides per pane. A revive is ESC, "keep going", Enter (ADR 0010/0031). Sessions that finished, are waiting for input, or errored before the outage are skipped, with the reason logged.

- **Claude Code** (experiments 0001-0002): death = `isApiErrorMessage` after the last user message + pane error + timing (ADR 0008/0018).
- **Codex CLI** (experiments 0003-0004): Codex retries on its own for ~5 min, then dies loudly at the prompt. Its rollout log records no error, so death = pane fingerprint + a rollout in the pane's cwd whose `task_complete` landed inside the outage window (ADR 0033). No pane-to-session mapping needed.

### Second host: bb (ADR 0036)

[bb](https://getbb.app) is a GUI that runs Claude Code and Codex as sessions, so there is no pane text. `host_bb.py` reads `bb thread list --json` and `bb thread log --json`: a dead thread has `status: error`, a final `provider/error` whose text is a network error, and that error landed inside the outage window. Revive is `bb thread tell <id> "keep going"`. Only `claude-code` and `codex` threads are in scope. Live-proven in experiment 0010: a real 8.5-min Wi-Fi cut, Claude A revived 2s after reconnect and ran to completion (ADR 0034).

### Hosts: cmux, Terminal.app, Ghostty

Terminal-style hosts share one contract (`available`, `list_targets`, `read_screen`, `resume`; see `host_cmux.py`). `watcher.py` runs the same decision loop over each host that is running. Adding a host = one ~100-line module + one entry in `TERMINAL_HOSTS`.

- **cmux** — `host_cmux.py`. cmux CLI reads the pane and sends keys. Full three-signal detection.
- **Terminal.app** — `host_terminal.py`. AppleScript (JXA) reads each tab's `contents` and `tty`; the harness comes from the process on that tty (`procs.py`). Revive is `do script "keep going"` (typed + Return; Terminal has no raw-key command, so no ESC). Full three-signal detection.
- **Ghostty** (>= 1.3) — `host_ghostty.py`. AppleScript can send `escape`, text, `enter`, but exposes no screen text. The harness comes from the title or a claude/codex process under Ghostty with the same cwd. Decision runs in two-signal mode (pane class `unreadable`): Claude = JSONL API error + timing; Codex = `task_complete` inside the outage window.

Both AppleScript hosts need macOS Automation permission once; the watcher never launches an app that is not running. Proven unattended under launchd in experiment 0008 (simulated outage, both hosts revived, zero false positives).

### Discord notifications

Every revive attempt (any host) posts one line to a Discord channel, e.g. `Revived: Claude Code in bb · offline 12m 15s · "Write Essay"`. `notify.py` reads the webhook URL from `~/.immortal-agents/discord_webhook` (or `DISCORD_WEBHOOK_URL`). No URL = no message; a failed post retries 3 times and never blocks recovery.

### Readiness gate and re-revive (experiment 0009)

A real Wi-Fi cut showed that "internet is back" is not "the agent's API is reachable": macOS kept a stale negative DNS entry for `api.anthropic.com` for minutes after reconnect, so the revive died again with ENOTFOUND. Two guards:

- `ready.py` — before any revive, wait until `api.anthropic.com` and `api.openai.com` resolve through the Mac's own resolver (cap 10 min).
- Recheck — after a revive, the watcher re-evaluates only the targets it revived every 60s for 5 min. If one is dead again, it gets "keep going" again, up to `MAX_REVIVES` (3) per outage. A recheck never wakes a target it did not revive.

## Layout (ADR 0043)

Flat modules: `watcher.py` (trigger, frozen), `detect*.py` (one per harness), `host_*.py` (one per host), `sim/` (simulated outage). The trigger is never re-tested; everything downstream is tested with a simulated outage while the Mac stays online.

## Simulated outage

Start the two local proxies in separate terminals:

```sh
python3 sim/proxy.py serve --port 10199 --upstream http://127.0.0.1:10100 --dead-mode 502
python3 sim/proxy.py serve --port 10198 --upstream https://api.anthropic.com --dead-mode drop
```

Launch test sessions with process-only endpoint overrides:

```sh
codex -c 'openai_base_url="http://127.0.0.1:10199/v1"'
ANTHROPIC_BASE_URL=http://127.0.0.1:10198 claude
```

`python3 sim.py on [--minutes N]` makes the watcher report offline and flips both proxies dead without cutting the Mac's internet. The flags expire after 10 minutes by default. `python3 sim.py off` revives both proxies and restores real probes. `python3 sim.py status` shows the watcher and proxy states.

`WATCHER_STATE_DIR` overrides the shared state directory for the watcher, simulator, and proxies. These commands do not change global Codex or Claude settings.

Install as a LaunchAgent: [docs/launchd.md](docs/launchd.md). Do not load the plist until you want it always on.
