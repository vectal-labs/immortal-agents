"""Confirm persistent native Codex retries from its logs and same-turn rollout."""

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from immortal.core import logbook, outcomes, telemetry
from immortal.core.common import parse_ts

RETRY_MARKERS = (
    "stream disconnected - retrying sampling request",
    "stream connection failed; waiting to retry",
    "remote compaction v2 stream failed; retrying request",
)
TURN_ID = re.compile(r"\bturn_id=\"?([a-zA-Z0-9_-]+)")
RECOVERY_ID = re.compile(r"\brecovery_id=\"?([a-zA-Z0-9_-]+)")
RECOVERY_EVENT = re.compile(r"(?:^|:\s)(recovery_started|recovery_confirmed|recovery_unconfirmed)(?=\s|$)")
BATCH_SIZE = 500


def _connect(path):
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _homes():
    values = [os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")]
    values.extend(filter(None, os.environ.get("IMMORTAL_CODEX_HOMES", "").split(os.pathsep)))
    return list(dict.fromkeys(str(Path(value).expanduser().resolve()) for value in values))


def _outputs(path, turn_id):
    """Yield only complete, timestamped records inside the warning's exact turn."""
    active = None
    with Path(path).open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                break
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(row, dict):
                continue
            payload = row.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            kind = payload.get("type")
            next_turn = None
            if row.get("type") == "turn_context" or (row.get("type") == "event_msg" and kind == "task_started"):
                next_turn = payload.get("turn_id")
            elif row.get("type") == "response_item":
                # Older native histories bind turns on response items instead of
                # emitting task_started/turn_context records.
                metadata = payload.get("internal_chat_message_metadata_passthrough")
                if isinstance(metadata, dict):
                    next_turn = metadata.get("turn_id")
            if isinstance(next_turn, str) and next_turn:
                at = parse_ts(row.get("timestamp"))
                if active == turn_id and next_turn != turn_id and at:
                    yield at.timestamp(), "closed"
                active = next_turn
            if active != turn_id:
                continue
            at = parse_ts(row.get("timestamp"))
            if at and outcomes._assistant_output("codex", row):
                yield at.timestamp(), "progress"
            if row.get("type") == "event_msg" and kind in ("task_complete", "turn_aborted"):
                if payload.get("turn_id") in (None, turn_id):
                    if at:
                        yield at.timestamp(), "closed"
                    active = None


def _attempt_id(home, generation, row_id):
    digest = hashlib.sha256(f"{home}:{generation}:{row_id}".encode()).hexdigest()
    return "native-codex-" + digest[:32]


def _same_session(watcher, pending):
    return (watcher.get("provider_session_id") == pending["session_id"]
            or bool(pending.get("path")) and watcher.get("path") == pending["path"])


def _same_turn(watcher, pending):
    turn = watcher.get("provider_turn_id") if watcher.get("host") == "bb" else watcher.get("turn_id")
    return turn in (None, pending["turn_id"])


def _stage(state, pending, retry, at):
    # A diagnostic write may arrive after the watcher already confirmed output.
    for receipt in state.get("recovery_confirmations", {}).values():
        if not _same_session(receipt, pending) or not _same_turn(receipt, pending):
            continue
        lower_bound = "accepted_at" if receipt.get("host") == "bb" else "sent_at"
        sent = parse_ts(receipt.get(lower_bound))
        if not sent and receipt.get("host") == "bb" and receipt.get("provider_turn_id"):
            sent = parse_ts(receipt.get("sent_at"))
        confirmed = parse_ts(receipt.get("confirmed_at"))
        if sent and confirmed and sent.timestamp() <= retry["at"] <= confirmed.timestamp():
            return None
    attempt_id = retry["attempt_id"]
    attempt = {"host": "codex", "ref": pending["session_id"], "harness": "codex",
               "label": pending["session_id"], "path": pending.get("path"),
               "turn_id": pending["turn_id"],
               "provider_session_id": pending["session_id"],
               "sent_at": datetime.fromtimestamp(retry["at"], timezone.utc).isoformat()}
    for watcher_id, watcher in list(state.get("pending_revives", {}).items()):
        if not isinstance(watcher, dict) or watcher.get("harness") != "codex":
            continue
        sent = parse_ts(watcher.get("accepted_at") or watcher.get("sent_at"))
        known_turn = watcher.get("provider_turn_id") if watcher.get("host") == "bb" else watcher.get("turn_id")
        if (_same_session(watcher, pending) and known_turn == pending["turn_id"]
                and sent and sent.timestamp() <= retry["at"] <= at):
            attempt_id, attempt = watcher_id, watcher
            break
    timestamp = datetime.fromtimestamp(at, timezone.utc)
    fields = outcomes.stage_complete(state, attempt_id, attempt, timestamp,
                                     "native_retry_progress", timestamp)
    if fields is not None:
        receipt = {"source": "native", "provider_session_id": pending["session_id"],
                   "turn_id": pending["turn_id"],
                   "sent_at": datetime.fromtimestamp(retry["at"], timezone.utc).isoformat(),
                   "confirmed_at": timestamp.isoformat()}
        if pending.get("path"):
            receipt["path"] = pending["path"]
        state.setdefault("recovery_confirmations", {})[attempt_id] = receipt
    return fields


def _native_event(state, source, session_id, turn_id, body, at, notices):
    event, identity = RECOVERY_EVENT.search(body), RECOVERY_ID.search(body)
    if not event or not identity:
        return
    recovery_id = identity.group(1)
    if recovery_id in source.get("completed_events", {}):
        return
    key = session_id + ":" + turn_id
    if event.group(1) == "recovery_started" and at <= source.get("confirmed", {}).get(key, -1):
        source.setdefault("completed_events", {})[recovery_id] = at
        return
    pending = source.setdefault("pending", {}).setdefault(key, {
        "session_id": session_id, "turn_id": turn_id, "retries": []})
    retry = next((item for item in pending["retries"] if item.get("recovery_id") == recovery_id), None)
    if retry is None:
        retry = {"attempt_id": "native-codex-" + recovery_id, "at": at, "recovery_id": recovery_id}
        pending["retries"].append(retry)
    if event.group(1) == "recovery_started":
        return
    if event.group(1) == "recovery_confirmed":
        fields = _stage(state, pending, retry, at)
        if fields:
            notices.append(fields)
        source.setdefault("confirmed", {})[key] = at
    source.setdefault("completed_events", {})[recovery_id] = at
    source.setdefault("resolved", {})[key] = at
    pending["retries"] = [item for item in pending["retries"] if item["at"] > at]
    if not pending["retries"]:
        source["pending"].pop(key)


def _scan_home(state, home, notices):
    source = state.setdefault("native_codex", {}).setdefault(str(home), {})
    logs = home / "logs_2.sqlite"
    stat = logs.stat()
    generation = f"{stat.st_dev}:{stat.st_ino}"
    with closing(_connect(logs)) as connection:
        maximum = connection.execute("SELECT COALESCE(MAX(id), 0) FROM logs").fetchone()[0]
        if "cursor" not in source:
            source.update(cursor=maximum, generation=generation, pending={})
            source.pop("error", None)
            return
        if source.get("generation") != generation or source["cursor"] > maximum:
            source.update(cursor=0, generation=generation)
        rows = connection.execute(
            "SELECT id, ts, ts_nanos, thread_id, feedback_log_body, target FROM logs "
            "WHERE id > ? AND target IN ('codex_core::responses_retry', 'codex_core::recovery_reporting') ORDER BY id LIMIT ?",
            (source["cursor"], BATCH_SIZE)).fetchall()
    for row_id, seconds, nanos, session_id, body, target in rows:
        source["cursor"] = row_id
        if not isinstance(body, str) or not isinstance(session_id, str) or not session_id:
            continue
        if target == "codex_core::responses_retry" and not any(marker in body for marker in RETRY_MARKERS):
            continue
        match = TURN_ID.search(body)
        if not match:
            continue
        turn_id = match.group(1)
        key = session_id + ":" + turn_id
        at = seconds + nanos / 1_000_000_000
        if target == "codex_core::recovery_reporting":
            _native_event(state, source, session_id, turn_id, body, at, notices)
            continue
        if at <= source.get("resolved", {}).get(key, -1):
            continue
        pending = source.setdefault("pending", {}).setdefault(key, {
            "session_id": session_id, "turn_id": turn_id, "retries": []})
        pending["retries"].append({"attempt_id": _attempt_id(home, generation, row_id),
                                   "at": at})
    # Don't rescan unrelated diagnostic rows forever. A full batch must drain first.
    if len(rows) < BATCH_SIZE:
        source["cursor"] = max(maximum, source["cursor"])
    source.pop("error", None)
    if not source.get("pending"):
        return
    with closing(_connect(home / "state_5.sqlite")) as connection:
        for key, pending in list(source["pending"].items()):
            row = connection.execute("SELECT rollout_path FROM threads WHERE id = ?",
                                     (pending["session_id"],)).fetchone()
            if not row:
                continue
            pending["path"] = row[0]
            try:
                outputs = list(_outputs(row[0], pending["turn_id"]))
            except OSError:
                continue
            retries = sorted(pending["retries"], key=lambda item: item["at"])
            for at, kind in outputs:
                eligible = [retry for retry in retries if retry["at"] <= at]
                if not eligible:
                    continue
                if kind == "progress":
                    fields = _stage(state, pending, eligible[0], at)
                    if fields:
                        notices.append(fields)
                source.setdefault("resolved", {})[key] = at
                if kind == "progress":
                    source.setdefault("confirmed", {})[key] = at
                    for retry in eligible:
                        if retry.get("recovery_id"):
                            source.setdefault("completed_events", {})[retry["recovery_id"]] = at
                # Several transport retries before progress are one recovery episode.
                retries = [retry for retry in retries if retry["at"] > at]
            if retries:
                pending["retries"] = retries
            else:
                source["pending"].pop(key)
    source.pop("error", None)


def tick(state, now=None, homes=None):
    """Watcher-owned, restart-safe observation; never sends chat or HTTP requests.

    First observation baselines existing logs, avoiding historical Discord spam.
    Missing/locked logs retain all pending work. Ephemeral Codex sessions have no
    durable rollout; newer builds report those through sanitized native events.
    """
    changed = deepcopy(state)
    notices = []
    for value in (_homes() if homes is None else homes):
        home = Path(value).expanduser().resolve()
        # Each home's scan is transactional too: transient failures cannot commit
        # half an observation or consume a retry without its pending record.
        candidate = deepcopy(changed)
        candidate_notices = []
        try:
            _scan_home(candidate, home, candidate_notices)
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            source = changed.setdefault("native_codex", {}).setdefault(str(home), {})
            if isinstance(exc, FileNotFoundError) and exc.filename == str(home / "logs_2.sqlite"):
                # No history exists at this cutover. The first new session must
                # be observed even if it finishes before the next watcher tick.
                source.setdefault("cursor", 0)
            source["error"] = "observation_unavailable"
            continue
        changed = candidate
        notices.extend(candidate_notices)
    if changed != state:
        logbook.save_state(changed)
        state.clear()
        state.update(changed)
    for fields in notices:
        try:
            logbook.log("revive_confirmed", **fields)
            telemetry.send("revive_confirmed", **fields)
        except OSError:
            pass
