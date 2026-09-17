"""Policy for the single revived table shared by every host and detector."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from immortal.core.common import parse_ts, iso_after
from immortal.core.logbook import save_state

MAX_REVIVES = 3
PROVIDER_EPISODE_SECS = 3600
MAX_PROVIDER_ERROR_ALERTS = 256


def _effective_tries(entry, reset_after):
    tries = entry.get("tries", 0)
    revived_at = parse_ts(entry.get("at"))
    if reset_after and revived_at:
        age = (datetime.now(timezone.utc) - revived_at).total_seconds()
        if age > reset_after:
            return 0
    return tries


def may_revive(state, key, mode):
    """Gate first, recheck, and provider attempts against the shared table."""
    entry = state.setdefault("revived", {}).get(key, {})
    if mode == "first":
        return _effective_tries(entry, None) == 0
    if mode == "recheck":
        # ADR 0051: first pass may skip during Codex grace (tries == 0).
        return _effective_tries(entry, None) < MAX_REVIVES
    if mode == "provider":
        return _effective_tries(entry, PROVIDER_EPISODE_SECS) < MAX_REVIVES
    raise ValueError(f"unknown revive mode: {mode}")


def mark_provider_error_seen(state, host, harness, detail):
    """Reserve one alert per distinct error, not per thread or retry timestamp."""
    message = " ".join(detail.casefold().split())
    # Codex reports the same account quota through two different messages.
    if (message.startswith("you have hit your chatgpt usage limit")
            or re.fullmatch(r"(?:codex error: )?the usage limit has been reached[.!]?", message)):
        message = "codex_usage_limit"
    message = re.sub(r"\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b", "<id>", message)
    message = re.sub(r"\brequest[_ -]?id\s*[:=]\s*[a-z0-9_-]+\b", "request_id=<id>", message)
    message = re.sub(r"\b(try again|retry) in ~?\d+(?:\.\d+)?\s*"
                     r"(?:milliseconds?|ms|seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)\b",
                     r"\1 in <delay>", message)
    fingerprint = hashlib.sha256(f"{host}\0{harness}\0{message}".encode()).hexdigest()
    previous = state.get("provider_error_alerts", [])
    if fingerprint in previous:
        return False
    alerts = [*previous[-(MAX_PROVIDER_ERROR_ALERTS - 1):], fingerprint]
    # Do not consume a new alert in memory if persistence fails.
    save_state({**state, "provider_error_alerts": alerts})
    state["provider_error_alerts"] = alerts
    return True


def target_key(host, target, info, window, mode):
    if mode == "provider":
        return f"{host.NAME}:{target['ref']}:provider"
    elif mode in ("first", "recheck"):
        path = info.get("path") if isinstance(info, dict) else None
        identity = path or f"{host.NAME}:{target['ref']}"
        return f"{identity}:{window[0]}"
    raise ValueError(f"unknown revive mode: {mode}")


def error_at(info):
    if not isinstance(info, dict):
        return None
    return info.get("error_at") or info.get("api_error_ts")


RETRY_DELAYS = (30, 60, 120)


def _matching_attempts(state, key, signature, target_identity):
    return [
        entry for saved_key, entry in state.setdefault("revived", {}).items()
        if (saved_key == key or target_identity and entry.get("target_identity") == target_identity)
        and entry.get("attempted_error", entry.get("error_at")) == signature
        and entry.get("tries", 0)
    ]


def attempt_allowed(state, key, mode, signature, now, target_identity=None):
    entry = state.setdefault("revived", {}).get(key, {})
    # Legacy Codex/Pi records had no error identity. Without proof of a new
    # failure, keep them handled until the next outage instead of sending twice.
    if entry.get("tries", 0) and "delivery" not in entry and not entry.get("error_at"):
        return False
    # Old entries recorded an already-handled error. Preserve that protection.
    for previous in _matching_attempts(state, key, signature, target_identity):
        if previous.get("delivery") != "not_sent":
            return False
        if previous.get("error_tries", previous.get("tries", 0)) >= MAX_REVIVES:
            return False
        due = parse_ts(previous.get("next_at"))
        if due and now < due:
            return False
    # A failed first send may be retried by a later pass, within the same cap.
    gate_mode = "recheck" if mode == "first" and entry.get("delivery") == "not_sent" else mode
    return may_revive(state, key, gate_mode)


def reserve(state, key, at, signature, mode, attempt_id, target_identity=None):
    entry = state.setdefault("revived", {}).get(key, {})
    reset = PROVIDER_EPISODE_SECS if mode == "provider" else None
    tries = _effective_tries(entry, reset) + 1
    # Carry the same failure's count across outage keys without resetting the
    # existing per-outage/provider cap for genuinely new failures.
    error_tries = 1 + max((previous.get("error_tries", previous.get("tries", 0))
                         for previous in _matching_attempts(state, key, signature, target_identity)), default=0)
    delay = RETRY_DELAYS[min(max(tries, error_tries) - 1, len(RETRY_DELAYS) - 1)]
    state["revived"][key] = {
        "tries": tries, "at": at, "error_at": signature,
        "attempted_error": signature, "delivery": "unknown", "attempt_id": attempt_id,
        "target_identity": target_identity, "error_tries": error_tries,
        "next_at": iso_after(parse_ts(at), delay),
    }
    save_state(state)


def delivered(state, key, delivery):
    entry = state["revived"][key]
    entry["delivery"] = delivery
    if delivery == "not_sent":
        # Slow discovery and CLI calls must not consume the retry delay.
        tries = max(entry["tries"], entry.get("error_tries", 0))
        delay = RETRY_DELAYS[min(tries - 1, len(RETRY_DELAYS) - 1)]
        entry["next_at"] = iso_after(datetime.now(timezone.utc), delay)
    save_state(state)
