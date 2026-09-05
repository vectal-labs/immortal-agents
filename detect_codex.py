"""Codex CLI death detection (ADR 0033): pane fingerprint + outage timing +
a rollout whose task_complete landed inside the outage window."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

CODEX_SESSIONS = Path.home() / ".codex" / "sessions"

# Both families: local-proxy 502 wording (experiment 0004) and upstream wording.
DEATH_FINGERPRINTS = (
    "unexpected status 502",
    "provider unreachable",
    "unable to connect",
    "is the computer able to access the url",
    "stream error",
    "stream disconnected before completion",
    "connection closed prematurely",
    "network error while contacting openai",
)
PANE_WAITING = (
    "would you like to run the following command",
    "do you want to allow",
    "approve this command",
    "yes, and don't ask again",
    "press enter to confirm",
)
# Codex TUI markers. cmux wraps lines, so match on whitespace-normalized text.
PANE_MARKERS = ("ask codex",)  # narrow panes truncate the prompt text
UNREADABLE = "unreadable"  # host cannot read pane text (see host_ghostty)
# A rollout counts as "recently active" if touched within this margin of the
# outage; keeps the session scan cheap on machines with thousands of rollouts.
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


def is_codex_pane(screen):
    flat = normalize(screen)
    return any(marker in flat for marker in PANE_MARKERS)


def classify_pane(screen):
    flat = normalize(screen)
    if any(token in flat for token in PANE_WAITING):
        return "waiting_user"
    if any(token in flat for token in DEATH_FINGERPRINTS):
        return "network_error"
    return "other"


def _cwd_variants(cwd):
    if not cwd:
        return set()
    raw = str(Path(cwd).resolve())
    variants = {raw, raw.replace("/private/tmp", "/tmp")}
    if raw.startswith("/tmp/"):
        variants.add("/private" + raw)
    return variants


def _rollout_cwd(path):
    try:
        with Path(path).open(errors="replace") as fh:
            for index, line in enumerate(fh):
                if index >= 40:
                    break
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = obj.get("payload") or {}
                if obj.get("type") == "session_meta" and isinstance(payload, dict):
                    return payload.get("cwd")
    except OSError:
        return None
    return None


def rollouts_for_cwd(cwd, loss_at):
    """Rollouts whose session_meta.cwd matches and that were touched near the outage."""
    wanted = _cwd_variants(cwd)
    loss = parse_ts(loss_at)
    if not wanted or not loss or not CODEX_SESSIONS.is_dir():
        return []
    cutoff = (loss - SCAN_MARGIN).timestamp()
    found = []
    for path in CODEX_SESSIONS.glob("*/*/*/rollout-*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        if _cwd_variants(_rollout_cwd(path)) & wanted:
            found.append(path)
    return sorted(found)


def summarize_rollout(path):
    last_started = None
    last_complete = None
    last_activity = None
    for line in Path(path).read_text(errors="replace").splitlines()[-400:]:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = parse_ts(obj.get("timestamp"))
        if ts:
            last_activity = ts
        payload = obj.get("payload") or {}
        if obj.get("type") != "event_msg" or not isinstance(payload, dict) or not ts:
            continue
        if payload.get("type") == "task_started":
            last_started = ts
        elif payload.get("type") == "task_complete":
            last_complete = ts
    return {
        "path": str(path),
        "last_task_started": last_started.isoformat() if last_started else None,
        "last_task_complete": last_complete.isoformat() if last_complete else None,
        "last_activity": last_activity.isoformat() if last_activity else None,
    }


def evaluate(cwd, pane_class, loss_at, recovery_at):
    """Return (decision, reasons, info). Resume only when all three signals agree."""
    loss = parse_ts(loss_at)
    recovery = parse_ts(recovery_at)
    info = {"rollouts": [], "dead": [], "finished_after": []}
    reasons = [f"pane={pane_class}"]
    if pane_class == "waiting_user":
        return "skip", reasons + ["waiting_for_user"], info
    if not loss or not recovery:
        return "skip", reasons + ["bad_outage_window"], info
    for path in rollouts_for_cwd(cwd, loss_at):
        summary = summarize_rollout(path)
        info["rollouts"].append(summary)
        complete = parse_ts(summary["last_task_complete"])
        if not complete:
            continue
        if loss <= complete <= recovery:
            # A genuine finish during a blackout is impossible (ADR 0033).
            info["dead"].append(summary["path"])
        elif complete > recovery:
            info["finished_after"].append(summary["path"])
    reasons.append(f"rollouts={len(info['rollouts'])}")
    reasons.append(f"task_complete_in_outage={len(info['dead'])}")
    if pane_class not in ("network_error", UNREADABLE):
        return "skip", reasons + ["pane_not_network_error"], info
    if not info["dead"]:
        if info["finished_after"]:
            return "skip", reasons + ["finished_after_recovery"], info
        return "skip", reasons + ["no_task_complete_in_outage"], info
    if pane_class == UNREADABLE:
        # Host cannot expose pane text (Ghostty); the impossible task_complete decides.
        return "resume", reasons + ["two_signals_agree"], info
    return "resume", reasons + ["all_three_agree"], info
