"""Optional diagnostics (flag: $STATE_DIR/telemetry). Stdlib only, sent in the background."""
import json, os, platform, subprocess, sys, threading, time, urllib.request, uuid
from pathlib import Path
from immortal.core.common import STATE_DIR, now_iso

TELEMETRY_URL = os.environ.get("TELEMETRY_URL", "https://immortal-agents-telemetry.onrender.com/event")
COMMIT = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                        cwd=os.path.dirname(__file__)).stdout.strip()
FLAG, MACHINE_ID, STAMP = (STATE_DIR / n for n in ("telemetry", "machine_id", "heartbeat"))
TESTING = "unittest" in sys.modules  # the suite runs revive_pass for real; never report that
enabled = lambda: not TESTING and FLAG.is_file() and FLAG.read_text().strip() == "on"
SOURCE_ROOT = Path(__file__).resolve().parents[2]


def _post(body):
    try:
        req = urllib.request.Request(TELEMETRY_URL, json.dumps(body).encode(), {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2).close()
    except Exception:
        pass


def send(event, **fields):
    """Fire-and-forget POST. Returns the thread, or None when telemetry is off."""
    try:
        if not enabled():
            return None
        MACHINE_ID.is_file() or MACHINE_ID.write_text(uuid.uuid4().hex)
        fields.update(event=event, machine_id=MACHINE_ID.read_text().strip(), macos=platform.mac_ver()[0],
                      python=platform.python_version(), commit=COMMIT, ts=now_iso())
        thread = threading.Thread(target=_post, args=(fields,), daemon=True)
        thread.start()
        return thread
    except Exception:
        return None


def heartbeat():
    if enabled() and not (STAMP.is_file() and time.time() - STAMP.stat().st_mtime < 86400):
        STAMP.touch()
        send("heartbeat")


def exception(exc):
    fields = {"type": type(exc).__name__, "file": None, "line": None}
    frame = exc.__traceback__
    while frame:
        try:
            path = Path(frame.tb_frame.f_code.co_filename).resolve().relative_to(SOURCE_ROOT)
            if path.parts[:1] == ("immortal",) or str(path) in ("watcher.py", "revive.py"):
                # Keep the innermost project frame, never a dependency or user path.
                fields.update(file=path.as_posix(), line=frame.tb_lineno)
        except (OSError, ValueError):
            pass
        frame = frame.tb_next
    return send("exception", **fields)
