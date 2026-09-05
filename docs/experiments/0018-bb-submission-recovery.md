# Experiment 0018 — Recover a rejected bb startup

September 5, 2026. All times UTC. No internet cut or shared bb restart.

## Method

Created disposable Codex thread `thr_b8vxdi2ruf`, with instructions only to echo
test markers. Identified its native Codex process by exact working directory and
executable. Paused only that process and submitted another marker. Waited for bb
to record the startup timeout, then terminated the disposable process.

The first preparation attempt established that bb's submission reply arrives
before provider startup finishes: terminating immediately caused an exit error.
The corrected experiment waited for the failed thread status before cleanup.

## Results

- 11:09:57.906: `system/error`: `JSON-RPC request timed out: turn/start`.
- Failed request: `creq_yenb7am4zn`. Its input was not accepted by Codex.
- 11:12:35.640: the new recovery pass logged `submission_retry_sent`, attempt 1.
- The thread returned exactly `RECOVERY_TIMEOUT_8642`, its original instruction.
- 11:12:45.700: `revive_confirmed`, reason `assistant_output`, elapsed 11.4 seconds.

All reads and recovery commands used the actual bb installation. Only the scan's
thread list was restricted to the disposable thread and watcher state was placed
in a temporary directory. Runtime release followed by guarded retry was also
verified live before this run.

This proves the submission-error parser, bb retry wiring, preservation of input,
and confirmation of output from the retried turn. The captured incident supplies
the separate `thread/resume` fingerprint; both fingerprints use the same path.

## Watcher activation

The primary checkout was updated locally and the existing launchd watcher was
restarted at 11:24:00 (PID 85977). A fresh timeout was injected into the same
disposable thread; recovery was then left entirely to launchd.

- 11:25:13.701: watcher observed the failure and logged `submission_retry_delay`.
- 11:25:48.621: watcher logged `submission_retry_sent`, attempt 1, request `creq_n7gddnz5kx`.
- The agent returned exactly `WATCHER_RECOVERED_5209`.
- 11:26:19.426: watcher logged `revive_confirmed`, matching assistant output after 8.4 seconds.

The thread returned to idle and its pending observation cleared. This proves
unattended recovery through the installed watcher with normal timing and live
bb commands. No manual retry was issued for this run.

The original incident was older than the 30-minute recovery window by activation.
The watcher correctly logged `submission_too_old`; one guarded manual retry of
its original request was then issued. bb started that request at 11:26:11.324.
The live acceptance event was `turn/input/accepted`, keyed by `clientRequestId`;
the parser and tests explicitly cover that actual bb contract.

The original thread subsequently completed its removal request and returned to
idle. A separate bb plugin inventory confirmed Tokenmaxx was absent. The
disposable test thread was archived and its runtime released after verification.
