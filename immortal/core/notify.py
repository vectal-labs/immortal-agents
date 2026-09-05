"""Discord webhook notifications for revives. Stdlib only, never raises."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.request

from immortal.core.common import STATE_DIR

# The URL is a secret: read from a file outside the repo, or the env var.
WEBHOOK_FILE = STATE_DIR / "discord_webhook"
HARNESS_NAMES = {
    "claude": "Claude Code",
    "claude-code": "Claude Code",
    "codex": "Codex",
    "pi": "Pi",
    "acp-cursor": "Cursor",
}
# Experiment 0009: the first post right after reconnect failed on stale DNS.
RETRY_DELAYS = (2, 5, 15)
MAX_PENDING_NOTIFICATIONS = 32
_notifications = queue.Queue(maxsize=MAX_PENDING_NOTIFICATIONS)
_worker = None
_worker_lock = threading.Lock()


def webhook_url():
    try:
        url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
        if not url and WEBHOOK_FILE.is_file():
            url = WEBHOOK_FILE.read_text().strip()
        return url or None
    except Exception:
        return None


def human_duration(secs):
    secs = int(secs or 0)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def revive_message(host, harness, offline_secs, label, ok, detail=None, trigger=None):
    name = HARNESS_NAMES.get(harness, harness or "agent")
    status = "Resume status unknown" if ok is None else ("Resume sent" if ok else "Revive FAILED")
    if trigger == "unhandled_provider_error":
        text = f"Unhandled provider error: {name} in {host}"
    elif trigger and trigger.startswith("bb_daemon_"):
        status = {"bb_daemon_resume_queued": "Resume queued",
                  "bb_daemon_recovery_confirmed": "Recovery confirmed",
                  "bb_daemon_recovery_unconfirmed": "Recovery unconfirmed"}.get(
                      trigger, status)
        text = f"{status}: {name} in {host} · BB daemon interruption"
    else:
        text = f"{status}: {name} in {host}"
        text += f" · {trigger.replace('_', ' ')}" if trigger else (
            f" · offline {human_duration(offline_secs)}"
        )
    if label:
        text += f' · "{label}"'
    if detail:
        limit = 200 if trigger == "unhandled_provider_error" else 120
        text += f" · {detail[:limit]}"
    return text


def post(url, text):
    try:
        body = json.dumps({"content": text[:1900]}).encode()
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json", "User-Agent": "immortal-agents"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _send_url(url, text, sleep):
    for delay in (*RETRY_DELAYS, None):
        if post(url, text):
            return True
        if delay is None:
            return False
        sleep(delay)


def send(text, sleep=time.sleep):
    """Post one line to Discord, retrying a few times. True on success; never raises."""
    try:
        url = webhook_url()
        return _send_url(url, text, sleep) if url else False
    except Exception:
        return False


def _deliver_notifications():
    while True:
        url, text = _notifications.get()
        try:
            _send_url(url, text, time.sleep)
        except Exception:
            pass
        finally:
            _notifications.task_done()


def notify_revive(host, harness, offline_secs, label, ok, detail=None, trigger=None):
    """Queue a best-effort notification. True means queued, not delivered."""
    global _worker
    try:
        url = webhook_url()
        if not url:
            return False
        text = revive_message(host, harness, offline_secs, label, ok, detail, trigger)[:1900]
        with _worker_lock:
            if _worker is None or not _worker.is_alive():
                worker = threading.Thread(
                    target=_deliver_notifications, name="immortal-discord", daemon=True
                )
                worker.start()
                _worker = worker
            # Drop new messages when Discord is stalled; recovery must never wait.
            _notifications.put_nowait((url, text))
        return True
    except Exception:
        return False
