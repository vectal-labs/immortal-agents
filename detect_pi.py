"""Pi coding-agent death detection: pane fingerprint + outage timing +
a session whose last message is a network error inside the outage window."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

PI_SESSIONS = Path.home() / ".pi" / "agent" / "sessions"

NETWORK_ERRORS = (
    "connection error",
    "connection refused",
    "connection lost",
    "network error",
    "fetch failed",
    "getaddrinfo",
    "enotfound",
    "eai_again",
    "socket hang up",
    "other side closed",
    "timed out",
    "timeout",
    "terminated",
)
DEATH_FINGERPRINTS = (
    "error: retry failed after 3 attempts:",
    *(f"error: {token}" for token in NETWORK_ERRORS),
)
# Title ("π - cwd") plus the default footer. Footer can be replaced by packages.
PANE_MARKERS = ("π -", "%/200k")
RETRYING = "retrying ("
UNREADABLE = "unreadable"
SCAN_MARGIN = timedelta(hours=6)


def normalize(text):
    return re.sub(r"\s+", " ", text or "").strip().lower()


def parse_ts(value):
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def encode_cwd(cwd):
    """Absolute path -> Pi session folder name(s). /Users/operator/code -> --Users-operator-code--."""
    if not cwd:
        return []
    raw = Path(cwd).resolve()
    variants = {raw, Path(str(raw).replace("/private/tmp", "/tmp"))}
    if str(raw).startswith("/tmp/"):
        variants.add(Path("/private" + str(raw)))
    return [f"--{str(path).strip('/').replace('/', '-')}--" for path in variants]


def is_pi_pane(screen):
    flat = normalize(screen)
    return any(marker in (screen or "") or marker in flat for marker in PANE_MARKERS)


def classify_pane(screen):
    flat = normalize(screen)
    if RETRYING in flat:
        return "other"
    if any(token in flat for token in DEATH_FINGERPRINTS):
        return "network_error"
    return "other"


def _network_error(message):
    text = message or ""
    if text.startswith("402") or text.startswith("429"):
        return False
    lower = text.lower()
    return any(token in lower for token in NETWORK_ERRORS)


def sessions_for_cwd(cwd, loss_at):
    """Newest-first session files for cwd, touched near the outage."""
    loss = parse_ts(loss_at)
    names = encode_cwd(cwd)
    if not names or not loss or not PI_SESSIONS.is_dir():
        return []
    cutoff = (loss - SCAN_MARGIN).timestamp()
    found = []
    for name in names:
        folder = PI_SESSIONS / name
        if not folder.is_dir():
            continue
        for path in folder.glob("*.jsonl"):
            try:
                if path.stat().st_mtime < cutoff:
                    continue
            except OSError:
                continue
            found.append(path)
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def summarize_session(path):
    last_user_ts = None
    last_role = last_stop = last_error = last_ts = None
    for line in Path(path).read_text(errors="replace").splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "message":
            continue
        msg = obj.get("message") or {}
        ts = parse_ts(obj.get("timestamp"))
        last_role, last_stop, last_error, last_ts = msg.get("role"), msg.get("stopReason"), msg.get("errorMessage"), ts
        if last_role == "user" and ts:
            last_user_ts = ts
    return {
        "path": str(path),
        "last_role": last_role,
        "last_stop_reason": last_stop,
        "last_error": last_error,
        "last_message_ts": last_ts.isoformat() if last_ts else None,
        "last_user_ts": last_user_ts.isoformat() if last_user_ts else None,
    }


def evaluate(cwd, pane_class, loss_at, recovery_at):
    """Return (decision, reasons, info). Resume only when all three signals agree."""
    loss, recovery = parse_ts(loss_at), parse_ts(recovery_at)
    info = {"sessions": [], "dead": []}
    reasons = [f"pane={pane_class}"]
    if not loss or not recovery:
        return "skip", reasons + ["bad_outage_window"], info
    sessions = sessions_for_cwd(cwd, loss_at)
    summary = summarize_session(sessions[0]) if sessions else None
    if summary:
        info["sessions"].append(summary)
    reasons.append(f"sessions={len(sessions)}")
    if not summary:
        return "skip", reasons + ["no_session"], info
    last_ts = parse_ts(summary["last_message_ts"])
    if summary["last_role"] == "user":
        return "skip", reasons + ["already_resumed"], info
    if summary["last_stop_reason"] == "aborted":
        return "skip", reasons + ["aborted"], info
    if summary["last_stop_reason"] in ("stop", "toolUse", "length") or not _network_error(summary["last_error"]):
        return "skip", reasons + ["error_not_network"], info
    if not last_ts or last_ts < loss:
        return "skip", reasons + ["stale_error"], info
    if not (loss <= last_ts <= recovery):
        return "skip", reasons + ["error_outside_outage"], info
    info["dead"].append(summary["path"])
    if pane_class not in ("network_error", UNREADABLE):
        return "skip", reasons + ["pane_not_network_error"], info
    if pane_class == UNREADABLE:
        return "resume", reasons + ["two_signals_agree"], info
    return "resume", reasons + ["all_three_agree"], info
