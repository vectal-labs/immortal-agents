"""Match post-recovery activity to its request, turn, and session boundaries."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from immortal.core.common import RESUME_TEXT, parse_ts

BB_TOOL_TYPES = {"commandExecution", "toolCall", "fileChange", "fileRead", "webFetch", "delegation"}


def obj(value):
    return value if isinstance(value, dict) else {}


def input_text(content):
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = [part.get("text", "") for part in content if isinstance(part, dict)
                 and part.get("type") in ("text", "input_text")]
        return "\n".join(text for text in texts if isinstance(text, str)).strip()
    return ""


def bb_output(attempt, events, has_text):
    since = parse_ts(attempt["sent_at"])
    baseline = attempt.get("bb_after_seq", 0)
    retry = attempt.get("bb_retry")
    request_id = turn_id = None
    other_requests = set()
    provider_turns = {}
    for row in events:
        if not isinstance(row, dict):
            continue
        data, scope = obj(row.get("data")), obj(row.get("scope"))
        native = (data.get("providerCheckpointId") if row.get("type") == "turn/completed" else
                  obj(obj(data.get("rawEvent")).get("params")).get("turnId")
                  if row.get("type") == "provider/unhandled" else None)
        if isinstance(scope.get("turnId"), str) and isinstance(native, str):
            provider_turns[scope["turnId"]] = native
    for row in events:
        if not isinstance(row, dict):
            continue
        seq = row.get("seq")
        if type(seq) is int and seq <= baseline:
            continue
        stamp = row.get("createdAt")
        try:
            at = datetime.fromtimestamp(stamp / 1000, timezone.utc) if type(stamp) in (int, float) else None
        except (ValueError, OverflowError, OSError):
            continue
        if at and at < since:
            continue
        data, scope = obj(row.get("data")), obj(row.get("scope"))
        kind = row.get("type")
        if (kind == "thread/identity" and attempt.get("provider_session_id")
                and data.get("providerThreadId")
                and data["providerThreadId"] != attempt["provider_session_id"]):
            attempt["finished_reason"] = "session_replaced"
            return None
        if kind == "client/turn/requested":
            matched = (data.get("retryOfRequestId") == retry["original_request_id"]
                       and data.get("retryAttempt") == retry["attempt"]) if retry else (
                           input_text(data.get("input")) == RESUME_TEXT)
            if matched and isinstance(data.get("requestId"), str) and data["requestId"]:
                request_id = data["requestId"]
            elif isinstance(data.get("requestId"), str):
                other_requests.add(data["requestId"])
        elif kind == "turn/input/accepted":
            if request_id and data.get("clientRequestId") == request_id:
                turn_id = scope.get("turnId")
                if not isinstance(turn_id, str) or not turn_id:
                    turn_id = None
                    continue
                attempt["turn_id"] = turn_id
                if at:
                    attempt["accepted_at"] = at.isoformat()
                if turn_id in provider_turns:
                    attempt["provider_turn_id"] = provider_turns[turn_id]
            elif data.get("clientRequestId") in other_requests and (turn_id or not request_id):
                attempt["finished_reason"] = "superseded"
                return None
        elif kind == "client/turn/rejected" and request_id and data.get("requestId") == request_id:
            attempt["finished_reason"] = "request_rejected"
            return None
        elif kind == "system/thread/interrupted" and at:
            attempt["finished_reason"] = "interrupted"
            return None
        if not turn_id or scope.get("turnId") != turn_id:
            continue
        item = obj(data.get("item"))
        if (at and kind in ("item/started", "item/completed")
                and (item.get("type") in BB_TOOL_TYPES
                     or item.get("type") == "agentMessage" and has_text(item.get("text")))):
            return at
        if kind == "turn/completed" and data.get("status") in ("completed", "failed", "interrupted"):
            attempt["finished_reason"] = "turn_" + str(data.get("status") or "ended")
            return None
        if kind == "provider/error" and data.get("willRetry") is False:
            attempt["finished_reason"] = "provider_failed"
            return None
    return None


def user_input(harness, row):
    message, payload = obj(row.get("message")), obj(row.get("payload"))
    if harness == "codex":
        if row.get("type") == "event_msg" and payload.get("type") == "user_message":
            return input_text(payload.get("message"))
        if row.get("type") == "response_item" and payload.get("role") == "user":
            metadata = obj(payload.get("internal_chat_message_metadata_passthrough"))
            kinds = metadata.get("content_item_kinds")
            if isinstance(kinds, list) and kinds and not any(
                    isinstance(kind, str) and kind.startswith("user.") for kind in kinds):
                return ""
            return input_text(payload.get("content"))
    elif ((row.get("type") == "user" or message.get("role") == "user")
          and not row.get("isMeta") and not row.get("isCompactSummary")):
        # Claude tool results are user rows, but have no user text blocks.
        return input_text(message.get("content"))
    return ""


def cli_output(attempt, assistant_output):
    since = parse_ts(attempt["sent_at"])
    with Path(attempt["path"]).open("rb") as fh:
        stat = os.fstat(fh.fileno())
        if ((attempt.get("inode") is not None and stat.st_ino != attempt["inode"])
                or stat.st_size < attempt.get("offset", 0)):
            attempt["finished_reason"] = "session_replaced"
            return None
        attempt["inode"] = stat.st_ino
        fh.seek(attempt.get("offset", 0))
        while line := fh.readline():
            # Retry a partial JSON record after the writer finishes it.
            if not line.endswith(b"\n"):
                break
            attempt["offset"] = fh.tell()
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(row, dict):
                continue
            at = parse_ts(row.get("timestamp"))
            if not at or at < since:
                continue
            payload = obj(row.get("payload"))
            metadata = obj(payload.get("internal_chat_message_metadata_passthrough"))
            kind = payload.get("type") if row.get("type") == "event_msg" else row.get("type")
            text = user_input(attempt["harness"], row)
            if (attempt["harness"] == "codex" and row.get("type") == "response_item"
                    and payload.get("role") == "user" and not metadata.get("content_item_kinds")):
                # Modern logs pair this with user_message; injected context can precede it.
                # Older logs have only response_item, so reconcile before accepting progress.
                attempt["pending_user_input"] = bool(text and text != RESUME_TEXT)
                text = ""
            elif kind == "user_message":
                attempt.pop("pending_user_input", None)
            if text and text != RESUME_TEXT:
                attempt["finished_reason"] = "superseded"
                return None
            if kind in ("task_started", "turn_context") or metadata.get("turn_id"):
                turn_id = payload.get("turn_id") or metadata.get("turn_id")
                if turn_id and attempt.get("turn_id") not in (None, turn_id):
                    attempt["finished_reason"] = "superseded"
                    return None
                if turn_id:
                    attempt["turn_id"] = turn_id
            if assistant_output(attempt["harness"], row):
                if attempt.pop("pending_user_input", False):
                    attempt["finished_reason"] = "superseded"
                    return None
                return at
            if (kind in ("task_complete", "turn_aborted")
                    or obj(row.get("message")).get("stopReason") == "aborted"):
                attempt["finished_reason"] = "turn_ended_without_progress"
                return None
    return None
