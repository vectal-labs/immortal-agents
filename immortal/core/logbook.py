"""JSON-lines log shared by the watcher and the host modules."""

from __future__ import annotations

import json
import os
import tempfile

from immortal.core.common import STATE_DIR, now_iso, parse_ts

LOG_PATH = STATE_DIR / "watcher.log"
STATE_PATH = STATE_DIR / "state.json"
LOG_CAP_BYTES = 5 * 1024 * 1024


def log(event, **fields):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    row = {"ts": now_iso(), "event": event, **fields}
    line = json.dumps(row, default=str)
    if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_CAP_BYTES:
        data = LOG_PATH.read_bytes()[-LOG_CAP_BYTES // 2:]
        LOG_PATH.write_bytes(data)
    with LOG_PATH.open("a") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def load_state():
    defaults = {"online": None, "outage_started_at": None, "revived": {}}
    try:
        state = json.loads(STATE_PATH.read_text())
    except FileNotFoundError:
        return defaults
    except (json.JSONDecodeError, UnicodeError) as exc:
        log("state_invalid", error=str(exc))
        return defaults
    if not isinstance(state, dict):
        log("state_invalid", error="state must be an object")
        return defaults

    if type(state.get("online")) is not bool:
        state["online"] = None
    for key in ("outage_started_at", "last_probe_at", "last_loss_at",
                "last_recovery_at", "last_api_ready_at", "provider_check_next_at"):
        if key in state or key in defaults:
            stamp = parse_ts(state.get(key))
            state[key] = now_iso(stamp) if stamp else None

    revived = state.get("revived")
    state["revived"] = {
        key: {**entry, "tries": entry.get("tries", 0)} for key, entry in (revived.items() if isinstance(revived, dict) else ())
        if isinstance(entry, dict)
        and type(entry.get("tries", 0)) is int and entry.get("tries", 0) >= 0
    }
    pending = state.get("pending_recovery")
    if pending is not None and not (
        isinstance(pending, dict)
        and all(parse_ts(pending.get(key)) for key in ("loss_at", "probe_recovery_at"))
        and type(pending.get("duration")) in (int, float)
        and 0 <= pending["duration"] < float("inf")
    ):
        state["pending_recovery"] = None
    recheck = state.get("recheck")
    if recheck is not None and not (
        isinstance(recheck, dict)
        and all(parse_ts(recheck.get(key)) for key in ("loss_at", "next_at", "until"))
        and type(recheck.get("duration")) in (int, float)
        and 0 <= recheck["duration"] < float("inf")
    ):
        state["recheck"] = None
    return state


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=STATE_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, STATE_PATH)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
