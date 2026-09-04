# 0009 — launchd LaunchAgent running a small polling script

## Context

ADR 0007 requires an ever-present process that tracks internet connectivity with timestamps. Options considered: launchd LaunchAgent + polling script, a Swift NWPathMonitor daemon, launchd's `KeepAlive.NetworkState` key, and cron/menubar apps.

Research findings: interface-level signals (NWPathMonitor, SCNetworkReachability, launchd `NetworkState`) can report "online" when there is no real internet (captive portals, router up but ISP down). Ground truth requires an actual HTTP probe. launchd's `NetworkState` key is old and unreliable. LaunchAgent + `KeepAlive` is the standard, proven way to keep a small user-level watcher alive.

## Decision

Run the watcher as a macOS launchd **LaunchAgent** (user-level, starts at login, `KeepAlive` auto-restarts it on crash). The watcher is a small script that polls real internet connectivity every ~15–30 seconds via an HTTP probe (e.g. Apple's captive-portal check endpoint) and logs every loss/recovery with a timestamp.

## Consequences

- Second-level precision on loss/recovery timestamps — good enough, since we target outages of a minute or longer (ADR 0002).
- Must be a LaunchAgent, not a LaunchDaemon: we need the user context for `~/.claude` and cmux.
- Known launchd pitfalls to handle: 10s restart throttle, `StandardOutPath`/`StandardErrorPath` for logging, PATH is not inherited.
