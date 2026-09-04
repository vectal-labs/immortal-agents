# 0013 — Run plainly in the foreground under launchd

## Context

The watcher used to self-daemonize: it forked twice, called `setsid`, ignored SIGHUP, and left a detached clone behind while the original exited. Under launchd (ADR 0009) this is fatal. launchd supervises the process it started; it sees the original exit as a crash and starts a new copy every 10 seconds. Each copy detaches too. Hundreds of watchers could pile up overnight, each able to resume sessions and send duplicate "keep going" prompts.

## Decision

- The watcher always runs as a plain foreground process. It never forks, never detaches, never ignores SIGHUP.
- launchd is the sole supported runner. The watcher is never run by hand; there are no detach flags or env vars.
- A second copy must refuse to start: a minimal `fcntl.flock` lock on a single lock file in the state dir. The second copy logs and exits nonzero. No PID files, no staleness checks.
- A guard test fails if `fork`/`setsid` code ever returns to `watcher.py`.

## Consequences

- launchd fully owns the lifecycle: start at login, restart on crash.
- Duplicate resume sends are impossible — one lock, one watcher.
- Backgrounding by hand is not a supported mode and is nobody's problem.
