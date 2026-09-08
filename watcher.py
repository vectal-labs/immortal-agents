#!/usr/bin/env python3
"""Ever-present internet watcher. Quiet to the user; verbose to the log file."""

from __future__ import annotations

import fcntl
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone

from immortal import __version__
from immortal.core import runtime

# Capture before importing recovery code. A later checkout edit cannot change this.
LOADED_CODE = runtime.identity()
LOADED_CODE["version"] = __version__

import revive
from immortal.core.common import STATE_DIR, now_iso
from immortal.core.logbook import load_state, log, save_state

PROBE_URL = "http://captive.apple.com/hotspot-detect.html"
PROBE_SECS = 10
MIN_OUTAGE_SECS = 120
LOCK_PATH = STATE_DIR / "watcher.lock"
SIMULATED_OUTAGE_PATH = STATE_DIR / "simulated_outage.json"


def simulated_outage_active():
    if not SIMULATED_OUTAGE_PATH.exists():
        return False
    try:
        flag = json.loads(SIMULATED_OUTAGE_PATH.read_text())
        expires_at = datetime.fromisoformat(flag["expires_at"].replace("Z", "+00:00"))
    except FileNotFoundError:
        return False
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        log("sim_invalid", error=str(exc))
        return False
    if expires_at <= datetime.now(timezone.utc):
        SIMULATED_OUTAGE_PATH.unlink(missing_ok=True)
        log("sim_expired", expires_at=flag["expires_at"])
        return False
    return flag.get("offline") is True


def probe():
    if simulated_outage_active():
        log("probe", online=False, simulated=True)
        return False
    result = revive.ready.check_internet(PROBE_URL)
    log("probe", **result)
    return result["online"]


def on_recovery(state, loss_at, recovery_at, duration):
    log("recovery", loss_at=loss_at, recovery_at=recovery_at, duration_secs=duration)
    if duration < MIN_OUTAGE_SECS:
        log("skip_recovery", reason="outage_too_short", duration_secs=duration)
        return
    revive.queue_recovery(state, loss_at, recovery_at, duration)


def acquire_single_instance_lock():
    """ADR 0013: exactly one watcher; a second copy logs and exits nonzero."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fh = LOCK_PATH.open("w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("already_running", lock=str(LOCK_PATH))
        sys.exit(1)
    return fh


def loop():
    log("start", probe_secs=PROBE_SECS, min_outage_secs=MIN_OUTAGE_SECS, pid=os.getpid())
    state = load_state()
    while True:
        try:
            online = probe()
            prev = state.get("online")
            if online is False and prev is not False:
                if not state.get("outage_started_at"):
                    detected_at = now_iso()
                    # 2026-09-03 (thr_vp7hipzyr6): the lid closed, the Mac slept
                    # 4 minutes, and the agent died 1.3s before the first failed
                    # probe on wake. Agents die in that gap, so the window opens
                    # at the last probe that passed.
                    loss_at = (state.get("last_probe_at") if prev else None) or detected_at
                    state["outage_started_at"] = loss_at
                    state["last_loss_at"] = loss_at
                    log("state_change", change="to_offline", at=loss_at, detected_at=detected_at, prev=prev)
            elif prev is False and online is True:
                recovery = now_iso()
                loss = state.get("outage_started_at")
                duration = 0
                if loss:
                    start = datetime.fromisoformat(loss.replace("Z", "+00:00"))
                    duration = (datetime.now(timezone.utc) - start).total_seconds()
                state["last_recovery_at"] = recovery
                log("state_change", change="offline_to_online", at=recovery, duration_secs=duration)
                on_recovery(state, loss, recovery, duration)
                state["outage_started_at"] = None
            state["online"] = online
            state["last_probe_at"] = now_iso()
            save_state(state)
            if online:
                try:
                    revive.run_recheck(state)
                except Exception as exc:
                    log("recovery_tick_error", tick="recheck", error=str(exc))
            # BB has its own per-endpoint evidence. Apple cannot veto it.
            try:
                revive.run_provider_check(state)
            except Exception as exc:
                log("recovery_tick_error", tick="provider", error=str(exc))
            save_state(state)
            if os.environ.get("WATCHER_ONCE"):
                log("once_exit")
                return
        except Exception as exc:
            log("loop_error", error=str(exc), tb=traceback.format_exc()[-800:])
        time.sleep(PROBE_SECS)


if __name__ == "__main__":
    try:
        lock_fh = acquire_single_instance_lock()
        if runtime.identity() != LOADED_CODE:
            raise RuntimeError("Watcher source changed during startup; restart required")
        runtime.record(LOADED_CODE)
        log("alive", pid=os.getpid(), ppid=os.getppid())
        loop()
    except Exception as exc:
        log("fatal", error=str(exc), tb=traceback.format_exc()[-800:])
        sys.stderr.write(traceback.format_exc())
        raise
