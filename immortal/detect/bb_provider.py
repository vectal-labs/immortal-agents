"""Detect recent whitelisted provider outages on bb error threads."""

from __future__ import annotations

from immortal.core.common import parse_ts
from immortal.detect.bb import is_network_error, is_provider_outage

NAME = "bb_provider"
PROVIDER_RETRY_DELAY_SECS = 120
PROVIDER_MAX_AGE_SECS = 30 * 60


def is_pane(screen):
    return is_provider_outage(screen)


def classify_pane(screen):
    if is_network_error(screen):
        return "network_error"
    if is_provider_outage(screen):
        return "provider_outage"
    return "other"


def evaluate(target, screen, window):
    """Return a decision using window recovery time as now; loss time is ignored."""
    now = parse_ts(window[1])
    status = target.get("status", "error")
    provider = target.get("harness_hint")
    reasons = [f"status={status}", f"provider={provider}"]
    info = {
        "thread_id": target.get("id"),
        "error_detail": screen,
        "error_at": target.get("error_at"),
    }
    error_at = parse_ts(info["error_at"])
    if status != "error":
        return "skip", reasons + ["not_error_status"], info
    if screen is None or not error_at or not now:
        return "unknown", reasons + ["no_provider_error"], info
    if is_network_error(screen):
        return "skip", reasons + ["network_error_belongs_to_outage_path"], info
    if not is_provider_outage(screen):
        return "unknown", reasons + ["error_not_whitelisted"], info
    age = (now - error_at).total_seconds()
    reasons.append(f"age_secs={int(age)}")
    if age > PROVIDER_MAX_AGE_SECS:
        return "skip", reasons + ["error_too_old"], info
    if age < PROVIDER_RETRY_DELAY_SECS:
        return "wait", reasons + ["retry_delay"], info
    return "resume", reasons + ["provider_outage"], info
