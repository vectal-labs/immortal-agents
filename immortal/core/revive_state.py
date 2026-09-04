"""Policy for the single revived table shared by every host and detector."""

from __future__ import annotations

from datetime import datetime, timezone

from immortal.core.common import parse_ts
from immortal.core.logbook import save_state

MAX_REVIVES = 3
PROVIDER_EPISODE_SECS = 3600


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


def mark_revived(state, key, at, error_at, mode):
    entry = state.setdefault("revived", {}).get(key, {})
    reset_after = PROVIDER_EPISODE_SECS if mode == "provider" else None
    state["revived"][key] = {
        "tries": _effective_tries(entry, reset_after) + 1,
        "at": at,
        "error_at": error_at,
    }
    save_state(state)


def mark_seen(state, key, error_at):
    entry = state.setdefault("revived", {}).get(key, {})
    state["revived"][key] = {
        "tries": entry.get("tries", 0),
        "at": entry.get("at"),
        "error_at": error_at,
    }


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
