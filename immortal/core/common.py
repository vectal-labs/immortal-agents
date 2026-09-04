"""Shared constants and small, side-effect-free helpers."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path(os.environ.get("WATCHER_STATE_DIR", Path.home() / ".immortal-agents"))
RESUME_TEXT = "keep going"


def parse_ts(value):
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)


def now_iso(value=None):
    timestamp = value or datetime.now(timezone.utc)
    return timestamp.isoformat().replace("+00:00", "Z")


def iso_after(timestamp, seconds):
    value = datetime.fromtimestamp(timestamp.timestamp() + seconds, tz=timezone.utc)
    return now_iso(value)


def normalize(text):
    return re.sub(r"\s+", " ", text or "").strip().lower()


def cwd_variants(cwd):
    """Resolve cwd and include macOS's equivalent /tmp and /private/tmp paths."""
    if not cwd:
        return set()
    raw = str(Path(cwd).resolve())
    variants = {raw, raw.replace("/private/tmp", "/tmp")}
    if raw.startswith("/tmp/"):
        variants.add("/private" + raw)
    return variants
