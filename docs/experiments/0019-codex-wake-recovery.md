# 0019 — Codex connection recovery

2026-09-08. Implemented and validated in isolated Codex and BB. Physical sleep/wake validation and normal adoption remain pending.

## Failure reproduced

Installed Codex 0.153.4 left a silent, open WebSocket waiting for **300.004 seconds**. It sent no client Ping. A recorded real incident waited about 275 seconds after Mac wake, then established a replacement connection in about 0.5 seconds. Sending `keep going` did not repair the old socket.

## Change

[Recovery patch](0019-codex-wake-recovery.patch), developed against upstream `74d3a5bf1046f004ee33a200ee497dc7593a5687`, applies unchanged to the installed release commit `3d2ee51ca2d5db578f328aa75e20aa22c0197c9a` (`rust-v0.153.4`).

- Check the existing socket every five seconds. Require an exact matching Pong within ten seconds.
- Native macOS wake triggers a fresh probe and deadline. Network hints trigger a probe without letting repeated notifications extend a pending deadline forever.
- Keep model-message inactivity and application-upload timeouts separate. A healthy quiet inference does not fail the transport check.
- Route a failed probe through Codex's existing retry, history, backoff and transport-fallback paths.
- Show the first `Reconnecting…` notification in release builds.
- Preserve queued Pongs across scheduler delays. Do not judge an old probe while an application upload prevented reading its reply.

The OS observer immediately acknowledges sleep requests. It does not inhibit sleep, change interfaces, or send chat messages.

## Validation

- API/HTTP-client tests: **291 passed, zero skipped**. All **17 transport regressions** also passed after the final queued-frame refinement.
- Existing Immortal Agents suite: **404 passed**; watcher logic is unchanged.
- Authenticated ChatGPT transport: **15/15 scenarios**, 29 exact matching Pongs, 26–54 ms RTT. No inference/tool requests. [Measurements](0019-codex-recovery/live-probe-results.json).
- Full Codex suite: **16,933 passed, 45 failed, 44 skipped**. Nine failed cases passed unchanged after correcting Python/PATH, color environment, or V8 feature selection. The remaining failures concern release-version expectations, global skills leaking into fixtures, and an existing shell-approval status mismatch. Unmodified release controls reproduce the MCP version, skills-warning, and shell-approval failures. No tests or snapshots were weakened. [Breakdown](0019-codex-recovery/validation.json).
- The full run preceded the final queued-frame refinement; the final targeted regressions and packaged checks cover that refinement. Scoped Clippy, formatting and diff checks passed.
- Final package: **14 transport cases and 2 public-RPC cases passed**. Silent-socket recovery took **15.376 seconds**, versus 300.004 seconds before. Quiet reasoning continued without retry. A healthy paused child with queued server Ping then matching Pong retained its connection. Cancellation worked; a completed command ran exactly once across retry and session restart/resume. [Results](0019-codex-recovery/e2e-results.json).
- Isolated BB 0.42.1: **15.313-second recovery**. BB persisted the first `Reconnecting... 1/1` event with `willRetry=true`, then completed the turn. Both native observers registered. The binary stayed unchanged, and all pilot processes/ports closed. This checks BB's persisted timeline, not pixel rendering. [Pilot result](0019-codex-recovery/bb-pilot-result.json).

## Reproduce

The [fixtures](0019-codex-recovery/) use disposable homes/workspaces and loopback servers. Tool cases need a host context where Codex can apply its own macOS sandbox.

```sh
python3 recovery_e2e.py --codex /path/to/package/bin/codex --expect patched
python3 app_server_e2e.py --codex /path/to/package/bin/codex --case cancel --expect patched
python3 app_server_e2e.py --codex /path/to/package/bin/codex --case tools_resume --expect patched
python3 recovery_pilot.py --codex-binary /path/to/package/bin/codex
```

The release tag contains a stale lockfile with workspace package versions `0.0.0`. The separate [build metadata patch](0019-codex-release-metadata.patch) synchronizes its 149 workspace versions to `0.153.4`. External dependencies and checksums are identical. This is separate from the behavior change.

The local build uses Rust 1.97.1 and the standard release profile. Codex's official package builder assembles the new CLI with the existing 0.153.4 helper, shell and ripgrep binaries. Building test helpers also requires the checksum-verified Codex V8 artifacts selected by `scripts/codex_package/v8.py`.

Package metadata identifies the candidate as `0.153.4+wake.1`; `codex --version` retains the compiled `0.153.4`. The native binary SHA-256 is `b278342ed1e6db758eb497cb706101dc64a513980d0c1def3f83536482539104`. [Source hashes](0019-codex-recovery/source-provenance.json) identify all eight changed files. The package is local to this BB thread; it is not an upstream release.

To rebuild, check out the exact release commit, apply both linked patches, then run `cargo build --locked --release -p codex-cli --bin codex` from `codex-rs`. Assemble with `scripts/build_codex_package.py`, supplying the new `--entrypoint-bin`, matching release helper inputs and `--package-version 0.153.4+wake.1`. [Package provenance](0019-codex-recovery/package-provenance.json) records helper hashes. Local build intermediates were removed after validation; source, package, logs and evidence remain.

## Limits and rollout

A Pong proves the connected WebSocket peer responds. It does not prove inference is progressing, or that the historical stalled connection would have failed this probe. A responsive socket with a stuck backend still uses the existing application timeout. Unseen model output may need regeneration; recorded tool results remain in history.

Process-only SIGSTOP/SIGCONT tests are not physical Mac sleep tests. A real sleep or network-cut experiment needs fresh approval under ADR 0037. No global Codex entrypoint, BB application bundle, active provider process, network setting, or power setting has been changed.

For any later normal rollout, keep a versioned package and an atomic, reversible entrypoint change. New app-server processes can adopt it without stopping running agents; already-cached processes remain on their current binary. Adopting the custom build deserves an approved ADR. This experiment does not create one.
