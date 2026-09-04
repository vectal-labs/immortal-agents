# Test drive

Check the install with an outage in a scratch session. `python3 -m immortal.sim`
fakes the watcher's probe and interrupts configured local proxies. Agents using
their normal API connection stay online. See the [simulation guide](launchd.md#simulated-outage).

## Level 1: one agent

Fastest harness: Claude Code in Terminal.app. It ships with macOS, the watcher
can read its screen, and the logs explain every decision.

1. Install, then run `./install.sh status` (loaded, running) and
   `./install.sh check` (Terminal: granted).
2. In Terminal.app, start `claude` in an empty scratch folder.
3. Paste a task that runs for a few minutes:

   ```text
   Build a Python maze solver here: generator, BFS solver, CLI, and 25+ unittest tests. Run the tests and fix until all pass. Do not ask me questions.
   ```

4. While Claude is working, turn Wi-Fi off. Unplug ethernet too.
5. Wait at least 2 minutes, then check whether Claude stopped with an outage error.
   Retry timing varies by agent and version; an agent may recover itself.
6. Turn Wi-Fi back on.
7. Once the provider API is ready, check for `keep going` and new Claude output.
   If it is still retrying or has already recovered, the watcher should leave it alone.

## Level 2: several agents in your real host

Same cut, but in the host you actually use (bb, cmux, Terminal, Ghostty).
Open two or three agents with mixed harnesses, give each the task above, and
leave one agent idle at its prompt. After recovery, agents with a recognized
outage failure should get `keep going`. Idle and surviving agents stay untouched.
Check the [tested combinations and limits](../README.md#support) first.

Before the cut, `./install.sh check` must show every open host as granted.
cmux also needs socket control mode set to `automation`.

## What a dead pane looks like

- Claude Code: `API Error` or `Connection lost`
- Codex: `provider unreachable`, `unable to connect`, or `stream error`
- Pi: `Error: Retry failed after 3 attempts: Connection error.`
- Cursor CLI (bb only): `PING timed out` in the last message

An agent that keeps retrying and never dies is a pass, not a failure. Recent
Codex builds survive very long cuts, and the watcher leaves live agents alone.

## Check the log

```sh
./install.sh logs
```

Expect `to_offline`, `offline_to_online`, then `decision` lines for evaluated agents.
Dead agents show `resume`. Skipped agents include a reason; idle bb threads may
not be evaluated. `resume_sent` means input was delivered. `revive_confirmed`
means new output was observed; `revive_unconfirmed` means it was not confirmed.
