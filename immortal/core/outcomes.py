"""Observe assistant output after a resume; sending input alone is not recovery."""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from immortal.core import telemetry, notify, discord_outbox
from immortal.core.common import now_iso, parse_ts
from immortal.core.logbook import log, save_state

OBSERVE_SECS = 600


def track(state, host, ref, harness, info, bb_events=None):
    sent_at = now_iso()
    if not isinstance(state.get("pending_revives"), dict):
        state["pending_revives"] = {}
    for attempt_id, previous in list(state["pending_revives"].items()):
        if (isinstance(previous, dict) and parse_ts(previous.get("sent_at"))
                and previous.get("host") == host and previous.get("ref") == ref):
            # Rechecks can retry before the next scheduled observation tick.
            try:
                at = _output_at(previous, bb_events)
            except Exception:
                at = None
            _finish(state, attempt_id, previous, at, "retried", parse_ts(sent_at))
    info = info or {}
    path = info.get("path")
    if not path:
        sessions = info.get("rollouts") or info.get("sessions") or []
        if len(sessions) == 1:
            path = sessions[0].get("path")
    attempt = {"host": host, "ref": ref, "harness": harness, "sent_at": sent_at}
    attempt["label"] = info.get("label") or ref
    attempt["discord_enabled"] = bool(notify.webhook_url())
    if info.get("bb_retry"):
        attempt["bb_retry"] = info["bb_retry"]
    if info.get("bb_interruption"):
        attempt["bb_interruption"] = info["bb_interruption"]
    if path:
        try:
            stat = Path(path).stat()
            attempt.update(path=str(path), offset=stat.st_size, inode=stat.st_ino)
        except OSError:
            pass
    attempt_id = uuid.uuid4().hex
    state.setdefault("pending_revives", {})[attempt_id] = attempt
    return attempt_id


def cancel(state, attempt_id):
    state.get("pending_revives", {}).pop(attempt_id, None)


def _has_text(content):
    if isinstance(content, str):
        return bool(content.strip()) and not content.lstrip().lower().startswith("error:")
    if isinstance(content, list):
        return any(isinstance(item, dict) and (
            item.get("type") in ("tool_use", "toolCall")
            or (item.get("type") in ("text", "output_text") and _has_text(item.get("text")))
        ) for item in content)
    return False


def _assistant_output(harness, row):
    message = row.get("message") or {}
    if harness == "claude":
        return (row.get("type") == "assistant" and not row.get("isApiErrorMessage")
                and isinstance(message, dict) and _has_text(message.get("content")))
    if harness == "pi":
        return (row.get("type") == "message" and isinstance(message, dict)
                and message.get("role") == "assistant"
                and message.get("stopReason") not in ("error", "aborted")
                and _has_text(message.get("content")))
    if harness == "codex":
        payload = row.get("payload") or {}
        if not isinstance(payload, dict):
            return False
        if row.get("type") == "response_item":
            if payload.get("type") in ("function_call", "custom_tool_call"):
                return isinstance(payload.get("name"), str) and bool(payload["name"].strip())
            return (payload.get("role") == "assistant" and _has_text(payload.get("content")))
        return (row.get("type") == "event_msg" and payload.get("type") == "agent_message"
                and _has_text(payload.get("message")))
    return False


def _output_at(attempt, bb_events):
    since = parse_ts(attempt["sent_at"])
    if attempt["host"] == "bb":
        retry = attempt.get("bb_retry")
        matched = not retry
        turn_id = None
        for row in bb_events(attempt["ref"]):
            data = row.get("data") or {}
            if retry and row.get("type") == "client/turn/requested":
                matched = (data.get("retryOfRequestId") == retry["original_request_id"]
                           and data.get("retryAttempt") == retry["attempt"])
                turn_id = None
            if retry and matched and row.get("type") == "turn/started":
                turn_id = (row.get("scope") or {}).get("turnId")
            if not matched:
                continue
            if row.get("type") != "item/completed":
                continue
            if retry and (not turn_id or (row.get("scope") or {}).get("turnId") != turn_id):
                continue
            stamp = row.get("createdAt")
            if not isinstance(stamp, (int, float)):
                continue
            at = datetime.fromtimestamp(stamp / 1000, timezone.utc)
            item = (row.get("data") or {}).get("item") or {}
            if (0 < (at - since).total_seconds() <= OBSERVE_SECS
                    and item.get("type") == "agentMessage" and _has_text(item.get("text"))):
                return at
    elif attempt.get("path"):
        with Path(attempt["path"]).open("rb") as fh:
            # A replaced/truncated file belongs to a different observation.
            stat = os.fstat(fh.fileno())
            if stat.st_ino != attempt["inode"] or stat.st_size < attempt["offset"]:
                return None
            fh.seek(attempt["offset"])
            for line in fh:
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(row, dict):
                    continue
                at = parse_ts(row.get("timestamp"))
                if (at and 0 < (at - since).total_seconds() <= OBSERVE_SECS
                        and _assistant_output(attempt["harness"], row)):
                    return at
    return None


def _finish(state, attempt_id, attempt, at, reason, now):
    event = "revive_confirmed" if at else "revive_unconfirmed"
    fields = dict(attempt_id=attempt_id, host=attempt.get("host"), harness=attempt.get("harness"),
                  reason="assistant_output" if at else reason,
                  elapsed_secs=round(((at or now) - parse_ts(attempt["sent_at"])).total_seconds(), 1))
    if at and (attempt.get("discord_enabled") or notify.webhook_url()):
        discord_outbox.stage(state, attempt_id, attempt.get("host"), attempt.get("harness"),
                             attempt.get("label") or attempt.get("bb_interruption", {}).get("label")
                             or attempt.get("ref"))
    cancel(state, attempt_id)
    save_state(state)
    log(event, **fields)
    telemetry.send(event, **fields)
    if not at and attempt.get("bb_interruption"):
        notify.notify_revive("bb", attempt.get("harness"), 0, attempt["bb_interruption"]["label"], bool(at),
                             detail=fields["reason"], trigger="bb_daemon_recovery_unconfirmed")


def check(state, bb_events, now=None):
    """Called on online ticks. Observation failure never prevents recovery."""
    now = now or datetime.now(timezone.utc)
    pending = state.get("pending_revives", {})
    if not isinstance(pending, dict):
        state["pending_revives"] = {}
        return
    for attempt_id, attempt in list(pending.items()):
        if not isinstance(attempt, dict) or not parse_ts(attempt.get("sent_at")):
            pending.pop(attempt_id, None)
            continue
        at = None
        reason = "no_output_observed"
        try:
            at = _output_at(attempt, bb_events)
        except Exception:
            reason = "observation_unavailable"
        since = parse_ts(attempt["sent_at"])
        observable = attempt.get("host") == "bb" or bool(attempt.get("path"))
        if not at and observable and (now - since).total_seconds() < OBSERVE_SECS:
            continue
        _finish(state, attempt_id, attempt, at, reason if observable else "no_session_log", now)
