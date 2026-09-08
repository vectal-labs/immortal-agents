"""Discord webhook notifications for revives. Stdlib only, never raises."""

from __future__ import annotations

import json
import math
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

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
_http_lock = threading.Lock()
_rate_limit_until = 0


@dataclass(frozen=True)
class Delivery:
    message_id: str | None = None
    error: str | None = None
    retry_after: float = 0
    permanent: bool = False

    def __bool__(self):
        return self.message_id is not None


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
    elif trigger == "recovery_confirmed":
        text = f"Recovery confirmed: {name} in {host}"
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


def _retry_after(exc):
    try:
        data = json.loads(exc.read(8192))
        value = data.get("retry_after") if isinstance(data, dict) else None
    except Exception:
        value = None
    header = exc.headers.get("Retry-After") if exc.headers else None
    for candidate in (value, header):
        try:
            seconds = float(candidate)
            if math.isfinite(seconds):
                return max(0.1, seconds)
        except (TypeError, ValueError):
            pass
    return 5


def post(url, text):
    """Require Discord's message acknowledgement; return only safe error codes."""
    global _rate_limit_until
    try:
        try:
            parts = urllib.parse.urlsplit(url)
        except ValueError:
            return Delivery(error="invalid_webhook", permanent=True)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return Delivery(error="invalid_webhook", permanent=True)
        query = [(key, value) for key, value in urllib.parse.parse_qsl(parts.query)
                 if key != "wait"]
        url = urllib.parse.urlunsplit(parts._replace(
            query=urllib.parse.urlencode([*query, ("wait", "true")]), fragment=""))
        body = json.dumps({"content": text[:1900], "allowed_mentions": {"parse": []}}).encode()
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json", "User-Agent": "immortal-agents"}
        )
        # Both notification workers share the same webhook rate limit.
        with _http_lock:
            delay = _rate_limit_until - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read(65536))
                    message_id = data.get("id") if isinstance(data, dict) else None
                    if 200 <= resp.status < 300 and isinstance(message_id, str) and message_id.isdigit():
                        return Delivery(message_id=message_id)
                    return Delivery(error="missing_message_ack")
            except urllib.error.HTTPError as exc:
                try:
                    if exc.code == 429:
                        delay = _retry_after(exc)
                        _rate_limit_until = time.monotonic() + delay
                        return Delivery(error="http_429", retry_after=delay)
                    return Delivery(error=f"http_{exc.code}", permanent=400 <= exc.code < 500
                                    and exc.code not in (408, 425))
                finally:
                    exc.close()
    except (ValueError, UnicodeError):
        return Delivery(error="invalid_response")
    except Exception:
        return Delivery(error="request_failed")


def _send_url(url, text, sleep):
    for delay in (*RETRY_DELAYS, None):
        result = post(url, text)
        if result:
            return True
        if delay is None or isinstance(result, Delivery) and result.permanent:
            return False
        sleep(max(delay, result.retry_after if isinstance(result, Delivery) else 0))


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
