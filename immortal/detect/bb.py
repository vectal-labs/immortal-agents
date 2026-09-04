"""Detect bb threads whose final provider error belongs to an outage window."""

from __future__ import annotations

from immortal.core.common import parse_ts

NAME = "bb"

NETWORK_FINGERPRINTS = (
    "can't reach the api server",
    "check your internet",
    "enotfound",
    "econnreset",
    "econnrefused",
    "fetch failed",
    "unexpected status 502",
    "provider unreachable",
    "stream error",
    "stream disconnected before completion",
    "network error while contacting openai",
    "connection error",
    "request timed out",
    "retry failed after",
    # Cursor (acp) in bb, 2026-09-03: "Error: RetriableError: Connection
    # stalled". Experiment 0011: "RetriableError: [internal]" and
    # "Connection failed. The connection failed 10 times."
    "connection stalled",
    "connection failed",
    "retriableerror: [internal]",
    # Experiment 0014: "Error: RetriableError: [unavailable] PING timed out".
    "ping timed out",
)
PROVIDER_OUTAGE_FINGERPRINTS = (
    "at capacity",
    "high demand",
    "temporarily unavailable",
    "did not respond",
    "overloaded",
)
def is_network_error(detail):
    flat = (detail or "").lower()
    return any(marker in flat for marker in NETWORK_FINGERPRINTS)


def is_provider_outage(detail):
    flat = (detail or "").lower()
    return any(marker in flat for marker in PROVIDER_OUTAGE_FINGERPRINTS)


def is_pane(screen):
    return is_network_error(screen) or is_provider_outage(screen)


def classify_pane(screen):
    if is_network_error(screen):
        return "network_error"
    if is_provider_outage(screen):
        return "provider_outage"
    return "other"


def _info(target, screen):
    return {
        "thread_id": target.get("id"),
        "error_detail": screen,
        "error_at": target.get("error_at"),
    }


def evaluate(target, screen, window):
    """Return (decision, reasons, info) for a target and ISO outage window."""
    loss_at, recovery_at = window
    status = target.get("status", "error")
    provider = target.get("harness_hint")
    reasons = [f"status={status}", f"provider={provider}"]
    info = _info(target, screen)
    if status != "error":
        return "skip", reasons + ["not_error_status"], info
    if screen is None:
        return "skip", reasons + ["no_provider_error"], info
    network = is_network_error(screen)
    reasons.append(f"network_error={network}")
    if not network:
        return "skip", reasons + ["error_not_network"], info
    loss, recovery, error_at = parse_ts(loss_at), parse_ts(recovery_at), parse_ts(info["error_at"])
    if not loss or not recovery or not error_at:
        return "skip", reasons + ["bad_outage_window"], info
    inside = loss <= error_at <= recovery
    reasons.append(f"error_in_outage={inside}")
    if not inside:
        return "skip", reasons + ["timing_miss"], info
    return "resume", reasons + ["all_three_agree"], info
