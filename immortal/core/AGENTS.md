# Shared recovery helpers
- `bb_recovery.py` owns rejected-submission retries: 30/60/120 seconds, three attempts per original bb request.
- Save retry reservations before commands. A lost CLI reply is ambiguous; reconcile bb state before any further action.
- `outcomes.py` confirms new assistant output. Queued or accepted input alone is not confirmed recovery.
- Keep credentials and original prompts out of retry state and telemetry.
- Test with a temporary `WATCHER_STATE_DIR`; never write test state into the live watcher directory.
- `updates.py` owns the hourly release notifier and separate `updates.json`/`updates.lock`; it never imports recovery or installs code. `updater.py` is user-invoked only, pins public tags to feed SHAs, refuses local work, and restarts only the watcher. Release procedure: `docs/launchd.md#publishing-an-important-update`.
- Daemon interruptions use `recover_interruption`: wait 30 seconds, maximum age 30 minutes, one reserved attempt per event and three per thread per rolling hour. Persist the observation before dispatch; never reset the daemon.
