"""Discord webhook notifications for revives. Stdlib only, never raises."""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

STATE_DIR = Path(os.environ.get("WATCHER_STATE_DIR", Path.home() / ".immortal-agents"))
# The URL is a secret: read from a file outside the repo, or the env var.
WEBHOOK_FILE = STATE_DIR / "discord_webhook"
HARNESS_NAMES = {"claude": "Claude Code", "claude-code": "Claude Code", "codex": "Codex"}
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


def revive_message(host, harness, offline_secs, label, ok):
    name = HARNESS_NAMES.get(harness, harness or "agent")
    status = "Revived" if ok else "Revive FAILED"
    text = f"{status}: {name} in {host} · offline {human_duration(offline_secs)}"
    if label:
        text += f' · "{label}"'
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


def notify_revive(host, harness, offline_secs, label, ok):
    return send(revive_message(host, harness, offline_secs, label, ok))
