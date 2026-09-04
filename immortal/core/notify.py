"""Discord webhook notifications for revives. Stdlib only, never raises."""

from __future__ import annotations

import json
import os
import time
import urllib.request

from immortal.core.common import STATE_DIR

# The URL is a secret: read from a file outside the repo, or the env var.
WEBHOOK_FILE = STATE_DIR / "discord_webhook"
HARNESS_NAMES = {
    "claude": "Claude Code",
    "claude-code": "Claude Code",
    "codex": "Codex",
    "pi": "Pi",
    "acp-cursor": "Cursor",
}
# Experiment 0009: the first post right after reconnect failed on stale DNS.
RETRY_DELAYS = (2, 5, 15)


def webhook_url():
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url and WEBHOOK_FILE.is_file():
        url = WEBHOOK_FILE.read_text().strip()
    return url or None


def human_duration(secs):
    secs = int(secs or 0)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def revive_message(host, harness, offline_secs, label, ok, detail=None, trigger=None):
    name = HARNESS_NAMES.get(harness, harness or "agent")
    if trigger == "unhandled_provider_error":
        text = f"Unhandled provider error: {name} in {host}"
    else:
        status = "Resume sent" if ok else "Revive FAILED"
        text = f"{status}: {name} in {host}"
        text += f" · {trigger.replace('_', ' ')}" if trigger else (
            f" · offline {human_duration(offline_secs)}"
        )
    if label:
        text += f' · "{label}"'
    if detail:
        limit = 200 if trigger == "unhandled_provider_error" else 120
        text += f" · {detail[:limit]}"
    return text


def post(url, text):
    body = json.dumps({"content": text[:1900]}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json", "User-Agent": "immortal-agents"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def send(text, sleep=time.sleep):
    """Post one line to Discord, retrying a few times. True on success; never raises."""
    url = webhook_url()
    if not url:
        return False
    for delay in (*RETRY_DELAYS, None):
        if post(url, text):
            return True
        if delay is None:
            return False
        sleep(delay)


def notify_revive(host, harness, offline_secs, label, ok, detail=None, trigger=None):
    return send(revive_message(host, harness, offline_secs, label, ok, detail, trigger))
