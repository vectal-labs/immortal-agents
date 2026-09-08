"""Observe assistant output after a resume; sending input alone is not recovery."""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from immortal.core import telemetry, notify, discord_outbox, recovery_events
from immortal.core.common import now_iso, parse_ts
from immortal.core.logbook import log, save_state


def track(state, host, ref, harness, info, bb_events=None):
    sent_at = now_iso()
    if not isinstance(state.get("pending_revives"), dict):
        state["pending_revives"] = {}
    for attempt_id, previous in list(state["pending_revives"].items()):
        if (isinstance(previous, dict) and parse_ts(previous.get("sent_at"))
                and previous.get("host") == host and previous.get("ref") == ref):
            # Rechecks can retry before the next scheduled observation tick.
            observed = dict(previous)
            try:
                at = _output_at(observed, bb_events)
            except Exception:
                at = None
            complete(state, attempt_id, observed, at, "retried", parse_ts(sent_at))
    info = info or {}
    path = info.get("path")
    if not path:
        sessions = info.get("rollouts") or info.get("sessions") or []
        if len(sessions) == 1:
            path = sessions[0].get("path")
    attempt = {"host": host, "ref": ref, "harness": harness, "sent_at": sent_at}
    attempt["label"] = info.get("label") or ref
    if info.get("bb_retry"):
        attempt["bb_retry"] = info["bb_retry"]
    if info.get("bb_interruption"):
        attempt["bb_interruption"] = info["bb_interruption"]
    if host == "bb" and bb_events:
        try:
            events = bb_events(ref)
            attempt["bb_after_seq"] = max((row.get("seq", 0) for row in events
                if isinstance(row, dict) and type(row.get("seq")) is int), default=0)
            for row in events:
                if isinstance(row, dict) and row.get("type") == "thread/identity":
                    session_id = recovery_events.obj(row.get("data")).get("providerThreadId")
                    if isinstance(session_id, str):
                        attempt["provider_session_id"] = session_id
        except Exception:
            pass
    if path:
        attempt.update(path=str(path), offset=0)
        try:
            stat = Path(path).stat()
            attempt.update(offset=stat.st_size, inode=stat.st_ino)
        except OSError:
            pass
    attempt_id = uuid.uuid4().hex
    state.setdefault("pending_revives", {})[attempt_id] = attempt
    return attempt_id


def cancel(state, attempt_id):
    state.get("pending_revives", {}).pop(attempt_id, None)


def _has_text(content):
    if isinstance(content, str):
        return bool(content.strip()) and not content.lstrip().lower().startswith(("error:", "api error:"))
    if isinstance(content, list):
        return any(isinstance(item, dict) and (
            item.get("type") in ("tool_use", "toolCall")
            or (item.get("type") in ("text", "output_text") and _has_text(item.get("text")))
        ) for item in content)
    return False


def _assistant_output(harness, row):
    message = row.get("message") or {}
    if harness in ("claude", "claude-code"):
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
    attempt.pop("finished_reason", None)
    if attempt["host"] == "bb":
        return recovery_events.bb_output(attempt, bb_events(attempt["ref"]), _has_text)
    if attempt.get("path"):
        return recovery_events.cli_output(attempt, _assistant_output)
    attempt["finished_reason"] = "no_session_log"
    return None


def stage_complete(state, attempt_id, attempt, at, reason, now):
    """Stage one outcome inside the caller's atomic recovery-state transaction."""
    fields = dict(attempt_id=attempt_id, host=attempt.get("host"), harness=attempt.get("harness"),
                  reason="assistant_output" if at else reason,
                  elapsed_secs=round(((at or now) - parse_ts(attempt["sent_at"])).total_seconds(), 1))
    duplicate = False
    if at:
        since = parse_ts(attempt.get("accepted_at") or attempt["sent_at"])
        for receipt in state.get("recovery_confirmations", {}).values():
            if receipt.get("source") != "native":
                continue
            same_session = (bool(attempt.get("provider_session_id"))
                            and attempt["provider_session_id"] == receipt.get("provider_session_id")
                            or bool(attempt.get("path")) and attempt["path"] == receipt.get("path"))
            native_turn = attempt.get("provider_turn_id") if attempt.get("host") == "bb" else attempt.get("turn_id")
            same_turn = (native_turn == receipt.get("turn_id") if native_turn else
                         attempt.get("host") == "bb" and bool(attempt.get("accepted_at")))
            started = parse_ts(receipt.get("sent_at"))
            if same_session and same_turn and started and since <= started <= at:
                duplicate = True
                break
    if at and not duplicate:
        discord_outbox.stage(state, attempt_id, attempt.get("host"), attempt.get("harness"),
                             attempt.get("label") or attempt.get("ref"))
    if (at and attempt.get("harness") == "codex"
            and attempt_id in state.get("pending_revives", {})
            and (attempt.get("path") or attempt.get("provider_session_id"))):
        # Native warnings can reach SQLite after the watcher already observed progress.
        state.setdefault("recovery_confirmations", {})[attempt_id] = {
            key: attempt[key] for key in ("host", "path", "provider_session_id", "turn_id", "provider_turn_id",
                                         "accepted_at", "sent_at") if key in attempt}
        state["recovery_confirmations"][attempt_id]["confirmed_at"] = at.isoformat()
    cancel(state, attempt_id)
    return None if duplicate else fields


def complete(state, attempt_id, attempt, at, reason, now, changes=None):
    """Save before clearing the live observation, even when the disk write fails."""
    updated = {**state, **(changes or {})}
    updated["pending_revives"] = dict(updated.get("pending_revives", {}))
    updated["discord_outbox"] = dict(updated.get("discord_outbox", {}))
    updated["recovery_confirmations"] = dict(updated.get("recovery_confirmations", {}))
    fields = stage_complete(updated, attempt_id, attempt, at, reason, now)
    save_state(updated)
    state.update(updated)
    if fields is None:
        return
    event = "revive_confirmed" if at else "revive_unconfirmed"
    try:
        log(event, **fields)
    except OSError:
        pass
    telemetry.send(event, **fields)
    if not at and attempt.get("bb_interruption"):
        notify.notify_revive("bb", attempt.get("harness"), 0, attempt["bb_interruption"]["label"], False,
                             detail=fields["reason"], trigger="bb_daemon_recovery_unconfirmed")


def check(state, bb_events, now=None):
    """Missing output or unavailable logs remain pending, including after restart."""
    now = now or datetime.now(timezone.utc)
    pending = state.get("pending_revives", {})
    if not isinstance(pending, dict):
        state["pending_revives"] = {}
        return
    for attempt_id, previous in list(pending.items()):
        if not isinstance(previous, dict) or not parse_ts(previous.get("sent_at")):
            pending.pop(attempt_id, None)
            continue
        attempt = dict(previous)
        try:
            at = _output_at(attempt, bb_events)
            attempt.pop("observation_error", None)
        except Exception:
            at = None
            attempt["observation_error"] = "observation_unavailable"
        if at or attempt.get("finished_reason"):
            complete(state, attempt_id, attempt, at, attempt.get("finished_reason"), now)
        elif attempt != previous:
            updated = {**state, "pending_revives": {**state["pending_revives"], attempt_id: attempt}}
            save_state(updated)
            state.update(updated)
