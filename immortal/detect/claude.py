"""Claude Code detector implementing the shared detector contract."""

from __future__ import annotations

import json
import re
from datetime import timezone
from pathlib import Path

from immortal.core.common import cwd_variants, normalize, parse_ts
from immortal.core.logbook import log

NAME = "claude"

PANE_NETWORK = (
    "API Error",
    "Connection lost",
    "check your network",
    "Waiting for API response",
)
PANE_WAITING = (
    "AskUserQuestion",
    "Do you want to proceed",
    "Yes, I trust this folder",
    "Permission request",
    "waiting for your",
    "Enter to select",
    "↑/↓ to navigate",
)

# Pane class for hosts that cannot read screen text (see host_ghostty).
UNREADABLE = "unreadable"

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CMUX_HOOK_SESSIONS = Path.home() / ".cmuxterm" / "claude-hook-sessions.json"
ACTIVITY_TYPES = {"user", "assistant"}
SKIP_USER_CONTENT = {"tool_result"}


def encode_cwd(cwd):
    if not cwd:
        return []
    names = []
    for path in sorted(cwd_variants(cwd)):
        # Claude also replaces punctuation (including dots and underscores).
        # Retain the previous spelling for older session directories.
        names.extend((re.sub(r"[^a-zA-Z0-9-]", "-", path), path.replace("/", "-")))
    return list(dict.fromkeys(names))


def jsonl_candidates(cwd):
    candidates = {}
    for encoded in encode_cwd(cwd):
        folder = CLAUDE_PROJECTS / encoded
        if folder.is_dir():
            for candidate in folder.glob("*.jsonl"):
                candidates[str(candidate)] = candidate
    return list(candidates.values())


def find_jsonl(cwd, surface_id):
    candidates = jsonl_candidates(cwd)
    session_id = None
    try:
        hooks = json.loads(CMUX_HOOK_SESSIONS.read_text())
        entry = hooks.get("activeSessionsBySurface", {}).get(surface_id, {})
        session_id = entry.get("sessionId")
    except (OSError, json.JSONDecodeError, AttributeError):
        pass

    if isinstance(session_id, str) and session_id:
        for encoded in encode_cwd(cwd):
            mapped = CLAUDE_PROJECTS / encoded / f"{session_id}.jsonl"
            if mapped.is_file():
                return mapped
        return None
    if len(candidates) == 1:
        return candidates[0]
    return None


def _text_from_message(obj):
    msg = obj.get("message") or {}
    content = msg.get("content")
    chunks = []
    kinds = []
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            kinds.append(item.get("type") or "?")
            if item.get("type") == "text":
                chunks.append(item.get("text") or "")
            if item.get("type") == "tool_use":
                chunks.append(item.get("name") or "")
    elif isinstance(content, str):
        chunks.append(content)
    return "\n".join(chunks), kinds


def summarize_jsonl(path):
    records = []
    for line in Path(path).read_text(errors="replace").splitlines()[-80:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    last_activity = None
    last_assistant = None
    last_user_ts = None
    api_error_ts = None
    waiting = False
    finished_clean = False
    for obj in records:
        ts = parse_ts(obj.get("timestamp"))
        typ = obj.get("type")
        if typ in ACTIVITY_TYPES and ts:
            last_activity = ts
        if typ == "assistant":
            last_assistant = obj
            text, kinds = _text_from_message(obj)
            if obj.get("isApiErrorMessage") and ts:
                if api_error_ts is None or ts > api_error_ts:
                    api_error_ts = ts
            if "AskUserQuestion" in kinds or "AskUserQuestion" in text:
                waiting = True
        if typ == "user":
            text, kinds = _text_from_message(obj)
            # tool_result is not a user message (ADR 0018)
            if ts and (not kinds or any(k not in SKIP_USER_CONTENT for k in kinds)):
                if last_user_ts is None or ts > last_user_ts:
                    last_user_ts = ts
            if "AskUserQuestion" in text:
                waiting = True
    stale_error = False
    api_error = False
    if api_error_ts:
        if last_user_ts is None or api_error_ts > last_user_ts:
            api_error = True
        else:
            stale_error = True
    if last_assistant and not api_error and not waiting:
        msg = last_assistant.get("message") or {}
        text, kinds = _text_from_message(last_assistant)
        if msg.get("stop_reason") == "end_turn" and "tool_use" not in kinds:
            if text.strip() and "API Error" not in text:
                finished_clean = True
    return {
        "path": str(path),
        "last_activity": last_activity.isoformat() if last_activity else None,
        "last_user": last_user_ts.isoformat() if last_user_ts else None,
        "api_error_ts": api_error_ts.isoformat() if api_error_ts else None,
        "api_error": api_error,
        "stale_error": stale_error,
        "waiting": waiting,
        "finished_clean": finished_clean,
    }


def classify_pane(screen):
    if any(token in screen for token in PANE_NETWORK):
        return "network_error"
    if any(token in screen for token in PANE_WAITING):
        return "waiting_user"
    return "other"


def timing_ok(last_activity_iso, loss_at, window_secs):
    if not last_activity_iso or not loss_at:
        return False
    last = parse_ts(last_activity_iso)
    loss = parse_ts(loss_at)
    if not last or not loss:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if loss.tzinfo is None:
        loss = loss.replace(tzinfo=timezone.utc)
    delta = (last - loss).total_seconds()
    # activity just before the drop, or the error stamp during the outage
    return -window_secs <= delta <= window_secs


def silent_hang_suspect(jsonl_info, loss_at, window_secs=30 * 60):
    """Outage overlapped, no error record, no JSONL activity after the drop."""
    if not jsonl_info or not loss_at:
        return False
    if jsonl_info.get("api_error") or jsonl_info.get("stale_error"):
        return False
    if jsonl_info.get("waiting") or jsonl_info.get("finished_clean"):
        return False
    last = parse_ts(jsonl_info.get("last_activity"))
    loss = parse_ts(loss_at)
    if not last or not loss:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if loss.tzinfo is None:
        loss = loss.replace(tzinfo=timezone.utc)
    delta = (last - loss).total_seconds()
    overlapped = -window_secs <= delta <= 0
    no_activity = last <= loss
    return overlapped and no_activity


def _decide(jsonl_info, pane_class, loss_at, window_secs=30 * 60):
    """Resume only when Claude's outage signals agree."""
    reasons = []
    if not jsonl_info:
        return "skip", ["no_jsonl"]
    timing = timing_ok(jsonl_info.get("last_activity"), loss_at, window_secs)
    reasons.append(f"timing={'yes' if timing else 'no'}")
    reasons.append(f"jsonl_api_error={jsonl_info.get('api_error')}")
    reasons.append(f"jsonl_stale_error={jsonl_info.get('stale_error')}")
    reasons.append(f"jsonl_waiting={jsonl_info.get('waiting')}")
    reasons.append(f"jsonl_finished_clean={jsonl_info.get('finished_clean')}")
    reasons.append(f"pane={pane_class}")
    if jsonl_info.get("stale_error"):
        return "skip", reasons + ["stale_error"]
    if jsonl_info.get("waiting") or pane_class == "waiting_user":
        return "skip", reasons + ["waiting_for_user"]
    if jsonl_info.get("finished_clean") and pane_class != "network_error":
        return "skip", reasons + ["finished_cleanly"]
    if jsonl_info.get("finished_clean") and not jsonl_info.get("api_error"):
        return "skip", reasons + ["finished_cleanly"]
    if not timing:
        return "skip", reasons + ["timing_miss"]
    if not jsonl_info.get("api_error"):
        if silent_hang_suspect(jsonl_info, loss_at, window_secs):
            return "skip", reasons + ["silent_hang_observed"]
        return "skip", reasons + ["jsonl_not_api_error"]
    if pane_class == UNREADABLE:
        # Host cannot expose pane text (Ghostty); JSONL error + timing decide.
        return "resume", reasons + ["two_signals_agree"]
    if pane_class != "network_error":
        return "skip", reasons + ["pane_not_network_error"]
    return "resume", reasons + ["all_three_agree"]


# Claude Code TUI markers (footer); Codex markers live in detect_codex.
CLAUDE_PANE_MARKERS = ("shift+tab to cycle", "⏵⏵")


def is_pane(screen):
    flat = normalize(screen)
    return any(marker in flat for marker in CLAUDE_PANE_MARKERS)


def evaluate(target, screen, window):
    """Return (decision, reasons, info) for a target and ISO outage window."""
    loss_at, _recovery_at = window
    jsonl = find_jsonl(target.get("cwd"), target.get("id"))
    if jsonl is None:
        log("no_session_mapping", ref=target.get("ref"), cwd=target.get("cwd"),
            candidate_count=len(jsonl_candidates(target.get("cwd"))))
    info = summarize_jsonl(jsonl) if jsonl else None
    pane_class = UNREADABLE if screen is None else classify_pane(screen)
    decision, reasons = _decide(info, pane_class, loss_at)
    return decision, reasons, info
