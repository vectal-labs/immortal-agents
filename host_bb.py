"""bb host (ADR 0036): find bb threads killed by internet loss via the bb CLI.

Death = thread status "error" + last provider/error detail matches a network
fingerprint + that error landed inside the outage window. Same three-signal
shape as the cmux hosts (ADR 0033)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone

# launchd's PATH has no bb, so default to the app bundle path (like CMUX).
BB = os.environ.get("BB_BIN") or os.environ.get("BB_CLI") or (
    "/Applications/bb.app/Contents/Resources/app.asar.unpacked/"
    "node_modules/bb-app/host-daemon/dist/bb"
)
PROVIDERS = ("claude-code", "codex", "pi")
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
    # Pi (2026-09-03): "Request timed out." then 3x "Connection error.".
    "connection error",
    "request timed out",
    "retry failed after",
)
# Provider-side outages while the Mac is online. Whitelist only:
# an unknown error is announced, never retried. Seen 2026-09-03 with Grok 4.6
# via OpenRouter in Pi.
PROVIDER_OUTAGE_FINGERPRINTS = (
    "at capacity",
    "high demand",
    "temporarily unavailable",
    "did not respond",
    "overloaded",
)
# xAI says "try again in a few minutes"; errors older than the max are stale
# (the user has moved on or the thread was abandoned).
PROVIDER_RETRY_DELAY_SECS = 120
PROVIDER_MAX_AGE_SECS = 30 * 60
RESUME_TEXT = "keep going"
# The bb launcher is `#!/usr/bin/env node`; launchd's PATH lacks Homebrew.
NODE_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


def _env():
    env = os.environ.copy()
    env["PATH"] = ":".join((*NODE_DIRS, env.get("PATH", "")))
    return env


class BbUnavailable(Exception):
    """bb binary missing or the bb server is not answering."""


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def _ms_to_dt(ms):
    if not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def bb_json(args):
    if shutil.which(BB) is None and not os.path.exists(BB):
        raise BbUnavailable(f"bb binary not found: {BB}")
    proc = subprocess.run(
        [BB, *args, "--json"], capture_output=True, text=True, timeout=30, env=_env()
    )
    if proc.returncode != 0:
        raise BbUnavailable(f"bb {' '.join(args)} failed: {proc.stderr.strip()[-300:]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise BbUnavailable(f"bb {' '.join(args)} returned non-JSON: {exc}")


def list_error_threads():
    """Error-status threads for the in-scope providers."""
    threads = bb_json(["thread", "list"])
    return [
        t
        for t in threads
        if t.get("status") == "error"
        and t.get("providerId") in PROVIDERS
        and not t.get("archivedAt")  # bb thread list includes archived threads
    ]


def thread_events(thread_id):
    return bb_json(["thread", "log", thread_id, "--all"])


def last_error(events):
    """The final provider/error of the last turn (retry notices are skipped)."""
    final = None
    for ev in events:
        if ev.get("type") != "provider/error":
            continue
        data = ev.get("data") or {}
        if data.get("willRetry"):
            continue
        final = {"detail": data.get("detail") or data.get("message") or "", "at": _ms_to_dt(ev.get("createdAt"))}
    return final


def is_network_error(detail):
    flat = (detail or "").lower()
    return any(marker in flat for marker in NETWORK_FINGERPRINTS)


def evaluate(thread, events, loss_at, recovery_at):
    """Return (decision, reasons, info). Resume only when all three signals agree."""
    reasons = [f"status={thread.get('status')}", f"provider={thread.get('providerId')}"]
    err = last_error(events)
    info = {
        "thread_id": thread.get("id"),
        "error_detail": err["detail"] if err else None,
        "error_at": err["at"].isoformat() if err and err["at"] else None,
    }
    if thread.get("status") != "error":
        return "skip", reasons + ["not_error_status"], info
    if not err:
        return "skip", reasons + ["no_provider_error"], info
    network = is_network_error(err["detail"])
    reasons.append(f"network_error={network}")
    if not network:
        return "skip", reasons + ["error_not_network"], info
    loss, recovery = parse_ts(loss_at), parse_ts(recovery_at)
    if not loss or not recovery or not err["at"]:
        return "skip", reasons + ["bad_outage_window"], info
    inside = loss <= err["at"] <= recovery
    reasons.append(f"error_in_outage={inside}")
    if not inside:
        return "skip", reasons + ["timing_miss"], info
    return "resume", reasons + ["all_three_agree"], info


def is_provider_outage(detail):
    flat = (detail or "").lower()
    return any(marker in flat for marker in PROVIDER_OUTAGE_FINGERPRINTS)


def evaluate_provider_outage(thread, events, now):
    """Online-path twin of evaluate(): no outage window, the error itself is the
    signal. Returns (decision, reasons, info); decision is resume, wait, skip
    or unknown (unknown = error status but no whitelisted phrase)."""
    reasons = [f"status={thread.get('status')}", f"provider={thread.get('providerId')}"]
    err = last_error(events)
    info = {
        "thread_id": thread.get("id"),
        "error_detail": err["detail"] if err else None,
        "error_at": err["at"].isoformat() if err and err["at"] else None,
    }
    if thread.get("status") != "error":
        return "skip", reasons + ["not_error_status"], info
    if not err or not err["at"]:
        return "skip", reasons + ["no_provider_error"], info
    if is_network_error(err["detail"]):
        return "skip", reasons + ["network_error_belongs_to_outage_path"], info
    if not is_provider_outage(err["detail"]):
        return "unknown", reasons + ["error_not_whitelisted"], info
    age = (now - err["at"]).total_seconds()
    reasons.append(f"age_secs={int(age)}")
    if age > PROVIDER_MAX_AGE_SECS:
        return "skip", reasons + ["error_too_old"], info
    if age < PROVIDER_RETRY_DELAY_SECS:
        return "wait", reasons + ["retry_delay"], info
    return "resume", reasons + ["provider_outage"], info


def resume(thread_id):
    proc = subprocess.run(
        # "auto" starts a new turn on an error thread; the default "steer"
        # returns HTTP 409 "Thread is not active" (experiment 0006).
        [BB, "thread", "tell", thread_id, RESUME_TEXT, "--mode", "auto"],
        capture_output=True,
        text=True,
        timeout=30,
        env=_env(),
    )
    return proc.returncode == 0, proc.stdout.strip()[-300:] or proc.stderr.strip()[-300:]
