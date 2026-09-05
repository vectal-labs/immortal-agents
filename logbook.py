"""JSON-lines log shared by the watcher and the host modules."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path(os.environ.get("WATCHER_STATE_DIR", Path.home() / ".immortal-agents"))
LOG_PATH = STATE_DIR / "watcher.log"
LOG_CAP_BYTES = 5 * 1024 * 1024


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def log(event, **fields):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    row = {"ts": now_iso(), "event": event, **fields}
    line = json.dumps(row, default=str)
    if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_CAP_BYTES:
        data = LOG_PATH.read_bytes()[-LOG_CAP_BYTES // 2 :]
        LOG_PATH.write_bytes(data)
    with LOG_PATH.open("a") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
