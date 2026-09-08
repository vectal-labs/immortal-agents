"""Durable success alerts. Only the watcher thread reads or writes recovery state."""

import hashlib
import json
import queue
import sys
import threading
import time

from immortal.core import logbook, notify
from immortal.core.common import now_iso

MAX_RECEIPTS = 100
_completed = queue.Queue(maxsize=1)
_inflight = None
_receipt = None


def stage(state, attempt_id, host, harness, label):
    """Add the alert to the caller's next atomic recovery-state save."""
    if attempt_id in state.get("discord_delivered", {}):
        return
    state.setdefault("discord_outbox", {}).setdefault(attempt_id, {
        "text": notify.revive_message(host, harness, 0, label, True, trigger="recovery_confirmed"),
        "event": "revive_confirmed", "created_at": now_iso(), "attempts": 0,
    })


def _deliver(attempt_id, fingerprint, url, text):
    try:
        result = notify.post(url, text)
    except Exception:
        result = notify.Delivery(error="worker_failed")
    _completed.put((attempt_id, fingerprint, result))


def _record(state, receipt, now):
    attempt_id, fingerprint, result = receipt
    pending = dict(state.get("discord_outbox", {}))
    if attempt_id not in pending:
        return
    entry = dict(pending[attempt_id])
    delivered = dict(state.get("discord_delivered", {}))
    health = dict(state.get("discord_status", {}))
    if health.get("webhook") != fingerprint:
        for key in ("blocked_webhook", "next_at", "error"):
            health.pop(key, None)
    entry.pop("blocked_webhook", None)
    entry["attempts"] += 1
    if result:
        pending.pop(attempt_id)
        delivered[attempt_id] = {"message_id": result.message_id, "at": now_iso()}
        delivered = dict(list(delivered.items())[-MAX_RECEIPTS:])
        health = {"last_delivered_at": now_iso(), "last_message_id": result.message_id}
        event = "discord_delivered"
    else:
        entry.update(error=result.error, next_at=now + max(
            result.retry_after, min(300, 2 ** min(entry["attempts"], 9))))
        if result.permanent:
            entry["blocked_webhook"] = fingerprint
        # Rotate failures so a large retry backlog cannot starve newer alerts.
        pending.pop(attempt_id)
        pending[attempt_id] = entry
        health.update(error=result.error, at=now_iso(), webhook=fingerprint)
        if result.error in ("http_401", "http_403", "http_404", "invalid_webhook"):
            health["blocked_webhook"] = fingerprint
        if result.error == "http_429":
            health["next_at"] = entry["next_at"]
        event = "discord_delivery_failed"
    changed = {"discord_outbox": pending, "discord_delivered": delivered, "discord_status": health}
    # Keep the receipt in memory until this save succeeds. A failed save must
    # not trigger another POST from the same process after Discord accepted it.
    logbook.save_state({**state, **changed})
    state.update(changed)
    try:
        logbook.log(event, attempt_id=attempt_id, message_id=result.message_id,
                    error=result.error, attempts=entry["attempts"])
    except OSError:
        pass  # The saved delivery state remains authoritative if logging fails.


def tick(state, now=None):
    """Drain receipts and start at most one HTTP request, without waiting for it."""
    global _inflight, _receipt
    now = time.time() if now is None else now
    if _receipt is None:
        try:
            _receipt = _completed.get_nowait()
        except queue.Empty:
            pass
    if _receipt is not None:
        _record(state, _receipt, now)
        _receipt = None
        _inflight = None
    retry = logbook.STATE_PATH.with_name("discord-retry")
    if retry.exists():
        pending = {}
        for key, entry in state.get("discord_outbox", {}).items():
            entry = dict(entry)
            if entry.get("blocked_webhook"):
                for field in ("blocked_webhook", "next_at", "error"):
                    entry.pop(field, None)
            pending[key] = entry
        health = {key: value for key, value in state.get("discord_status", {}).items()
                  if key != "blocked_webhook"}
        if state.get("discord_status", {}).get("blocked_webhook"):
            health.pop("error", None)
        changed = {"discord_outbox": pending, "discord_status": health}
        logbook.save_state({**state, **changed})
        state.update(changed)
        retry.unlink(missing_ok=True)
    if _inflight is not None or not state.get("discord_outbox"):
        return
    url = notify.webhook_url()
    if not url:
        health = dict(state.get("discord_status", {}))
        if health.get("error") != "webhook_missing":
            health.update(error="webhook_missing", at=now_iso())
            logbook.save_state({**state, "discord_status": health})
            state["discord_status"] = health
            logbook.log("discord_delivery_paused", reason="webhook_missing")
        return
    fingerprint = hashlib.sha256(url.encode()).hexdigest()
    health = state.get("discord_status", {})
    if (health.get("blocked_webhook") == fingerprint
            or health.get("webhook") == fingerprint and health.get("next_at", 0) > now):
        return
    for attempt_id, entry in state["discord_outbox"].items():
        if entry.get("blocked_webhook") == fingerprint:
            continue
        if entry.get("next_at", 0) > now and not entry.get("blocked_webhook"):
            continue
        # Persist before dispatch, including when a previous state save failed.
        logbook.save_state(state)
        worker = threading.Thread(target=_deliver, args=(attempt_id, fingerprint, url, entry["text"]),
                                  name="immortal-discord-outbox", daemon=True)
        worker.start()
        _inflight = attempt_id
        return


def status():
    """Read-only status, without starting workers or printing webhook secrets."""
    try:
        state = json.loads(logbook.STATE_PATH.read_text())
    except FileNotFoundError:
        state = {}
    except (ValueError, OSError):
        return "Discord: cannot read delivery state"
    if not isinstance(state, dict):
        return "Discord: invalid delivery state"
    pending = state.get("discord_outbox", {})
    health = state.get("discord_status", {})
    if not isinstance(pending, dict) or not isinstance(health, dict):
        return "Discord: invalid delivery state"
    if not notify.webhook_url():
        return f"Discord: not configured; {len(pending)} pending success alerts"
    text = f"Discord: configured; {len(pending)} pending success alerts"
    errors = sorted({entry.get("error") for entry in pending.values()
                     if isinstance(entry, dict) and entry.get("error")})
    if errors:
        text += "; delivery error: " + ", ".join(errors)
    if any(isinstance(entry, dict) and entry.get("blocked_webhook") for entry in pending.values()):
        text += "; paused: fix the webhook, then run ./install.sh discord-retry"
    if health.get("last_delivered_at"):
        text += f"; last delivered: {health['last_delivered_at']}"
    return text


if __name__ == "__main__":
    if sys.argv[1:] == ["--retry"]:
        # Signal the watcher instead of racing its ownership of state.json.
        retry = logbook.STATE_PATH.with_name("discord-retry")
        retry.parent.mkdir(parents=True, exist_ok=True)
        retry.touch()
        print("Discord retry requested; the running watcher will retry saved success alerts.")
    elif not sys.argv[1:]:
        print(status())
    else:
        sys.exit("usage: python3 -m immortal.core.discord_outbox [--retry]")
