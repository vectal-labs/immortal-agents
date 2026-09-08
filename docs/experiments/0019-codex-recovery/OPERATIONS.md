# Codex recovery E2E harness

Uses an actual Codex executable and a standard-library loopback Responses server.
Every run gets an isolated `CODEX_HOME` and work directory. No credentials are
provided. No production threads, normal config, machine sleep, or network settings
are changed. The environment excludes inherited provider/API keys and proxies.

## Actual baseline

Installed `codex-cli 0.153.4` waited **300.0042 seconds** after `response.created`
before abandoning a silent open socket. It sent **zero client pings**. The fixture
then completed its HTTPS-fallback path over local HTTP (the CLI's naming).
This baseline retains the real 300,000ms application deadline but sets stream
retries to zero so it exits after measuring the first wait. It does not claim to
reproduce the production retry count, authentication, or macOS sleep clock.

- `runs/20260908-134938-8b52e2/report.json`: full five-minute baseline.
- `runs/20260908-134939-93eeb8/application_idle/result.json`: application deadline
  at 1.5074s despite three successful server Ping/client Pong exchanges.
- `runs/20260908-134939-93eeb8/json_keepalive/result.json`: same delayed completion
  succeeds when application JSON events reset the application inactivity timer.
- `runs/20260908-135216-d33859/tools/result.json`: completed real shell command
  survives a stream retry and runs exactly once.
- `runs/20260908-135405-rpc-e38ded/result.json`: public RPC cancellation and next turn.
- `runs/20260908-135409-rpc-fe5c78/result.json`: app-server restart, resume same
  persisted session, completed command preserved exactly once.

Early development runs are retained, including failed tool tests caused by the
outer sandbox rejecting a nested `sandbox-exec`. A fake model's final success
message does not count as proof that a command ran: the fixture checks its file.

## Preflight controls

The preliminary release `0.153.4+wake.preflight`, SHA-256
`11accefbcba3e06de7b5828d345520d80096ba25d241a0178c775490995eaef4`, recovered the
silent peer in 15.3404s and passed quiet reasoning, peer close, real-tool retry,
RPC cancellation, and RPC restart/resume. Reports are in `preflight-runs/`.

It deliberately lacks the final queued-frame drain refinement. The added
`suspend_queued_pong` case catches this: the preliminary binary abandoned a healthy
connection 0.3421s after resuming, although the fixture had queued a server Ping
immediately followed by the correct Pong while the child was paused. The test
fails its one-connection and no-notification assertions even though the CLI later
returns success. See `preflight-runs/20260908-150124-0fb8da/report.json`.
This preflight result is not final-artifact validation.

## Patched binary

From this directory:

```sh
python3 -B recovery_e2e.py --codex /absolute/path/to/patched/codex --expect patched
python3 -B app_server_e2e.py --codex /absolute/path/to/patched/codex --case cancel --expect patched
python3 -B app_server_e2e.py --codex /absolute/path/to/patched/codex --case tools_resume --expect patched
```

The tool cases need a host context where Codex can apply its own macOS sandbox.
They execute only a fixture-selected `printf 'once\n' >> completed-tool.txt` in the
fresh workspace, with approval policy `never` and workspace-write sandbox.
To minimize host execution, run the CLI's thirteen non-tool cases in the ordinary
sandbox with `--cases suspend_queued_pong,silent,quiet,wrong_pong,server_ping,delayed_pong,late_pong,server_ping_no_pong,close,cancel,suspend,application_idle,json_keepalive`,
then run only `--cases tools` in host context. RPC `cancel` uses the ordinary
sandbox; RPC `tools_resume` uses host context.

Reports record the exact binary SHA-256 and verify it did not change during each
suite. First-retry visibility requires a structured CLI stdout error event with
`Reconnecting... 1/1`; stderr logging alone does not satisfy it. The fixture checks
that exactly one WebSocket retry occurred. The RPC resume test separately checks
the `error` notification's `willRetry`, thread/turn identity, and arrival before
the retry request. Tool checks require one successful command notification, one
file line, and the same completed tool output in both retry and resumed history.

Default transport checks expect one probe every 5s, matching Pong within 10s,
and reconnect in under 17s including local retry jitter. Healthy quiet responses
finish at 18s without retries. The application idle timeout remains 45s for these
cases. Two separate 1.5s controls verify that application inactivity stays bounded.
The default suite runs independent cases four at a time.

Cases: silent peer; healthy quiet peer; wrong Pong; incoming server Ping; delayed
matching Pong; Pong too late; server Ping without a matching Pong; explicit peer
Close; CLI cancellation; test-process suspension; completed local tool; application
idle control; application JSON keepalive control; healthy suspended child with
a server Ping immediately followed by the matching delayed Pong queued during
suspension. The last case requires one connection, zero retry notifications, and
successful completion; it verifies that the frames actually queued while paused.

`suspend` pauses **only the native child created by the fixture**, using
SIGSTOP/SIGCONT for 20s. The computer and all other processes remain awake. This
tests delayed scheduling; it does not claim to exercise native macOS wake or
network-change callbacks. The npm launcher is resolved to its installed native
executable for this case. Real OS sleep/wake still requires separate approval.

Override `--cases`, `--quiet-seconds`, `--pong-delay`, `--idle-ms`, `--case-timeout`,
`--suspend-after`, `--suspend-seconds`, or `--jobs` to bound individual scenarios.
To repeat the actual default application timeout:

```sh
python3 -B recovery_e2e.py --codex /absolute/path/to/unpatched/codex \
  --cases baseline_real --case-timeout 330 --jobs 1
```

## Isolated BB pilot

```sh
python3 -B mock_peer.py --case silent --seconds 120 --output runs/pilot-endpoint.json
```

Pass its emitted `codex_args` list to the separate BB pilot driver's `--codex-args`
JSON argument. Select `gpt-6-astra` and expected output `RECOVERY_OK`. The server
automatically stops at its deadline. Its full event record is written at exit.

## Boundaries

This is a synthetic network peer, not an OpenAI backend. It proves actual client
control-frame, timeout, retry, cancellation, and history behavior. It does not
prove a production ChatGPT connection always answers Ping, inference survives a
disconnection, or a real Mac wake notification is delivered. Recorded tool output
is restored; unseen server-side work can still need resampling after reconnect.
