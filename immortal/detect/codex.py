"""Codex CLI detector implementing the shared detector contract (ADR 0033, 0051)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from immortal.core.common import cwd_variants as _cwd_variants
from immortal.core.common import normalize, parse_ts

NAME = "codex"
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
# Visible work. Never a death fingerprint (ADR 0051 / experiment 0017).
PANE_ALIVE = ("esc to interrupt",)
# Codex TUI markers. cmux wraps lines, so match on whitespace-normalized text.
PANE_MARKERS = ("ask codex",)  # narrow panes truncate the prompt text
UNREADABLE = "unreadable"  # host cannot read pane text (see host_ghostty)
# A rollout counts as "recently active" if touched within this margin of the
# outage; keeps the session scan cheap on machines with thousands of rollouts.
SCAN_MARGIN = timedelta(hours=6)
# Experiment 0017: an alive Codex stays silent 5-7 min after recovery.
GRACE_SECS = 600


def is_pane(screen):
    flat = normalize(screen)
    return any(marker in flat for marker in PANE_MARKERS)


def classify_pane(screen):
    flat = normalize(screen)
    if any(token in flat for token in PANE_WAITING):
        return "waiting_user"
    if any(token in flat for token in PANE_ALIVE):
        return "alive"
    if any(token in flat for token in DEATH_FINGERPRINTS):
        return "network_error"
    return "other"


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


def evaluate(target, screen, window):
    """Return (decision, reasons, info) for a target and ISO outage window."""
    loss_at, window_end = window
    cwd = target.get("cwd")
    pane_class = UNREADABLE if screen is None else classify_pane(screen)
    loss = parse_ts(loss_at)
    now = parse_ts(window_end)
    recovery = parse_ts(target.get("recovery_at") or window_end)
    info = {"rollouts": [], "dead": [], "finished_after": []}
    reasons = [f"pane={pane_class}"]
    if pane_class == "waiting_user":
        return "skip", reasons + ["waiting_for_user"], info
    if pane_class == "alive":
        return "skip", reasons + ["pane_alive"], info
    if not loss or not recovery or not now:
        return "skip", reasons + ["bad_outage_window"], info
    paths = rollouts_for_cwd(cwd, loss_at)
    # A folder is not a session ID. Never mix another session's state into
    # the decision to send input to this terminal.
    if len(paths) > 1:
        return "skip", reasons + ["ambiguous_session"], info
    unfinished = False
    wrote_after = False
    for path in paths:
        summary = summarize_rollout(path)
        info["rollouts"].append(summary)
        started = parse_ts(summary["last_task_started"])
        complete = parse_ts(summary["last_task_complete"])
        activity = parse_ts(summary["last_activity"])
        if complete:
            if loss <= complete <= recovery:
                # A genuine finish during a blackout is impossible (ADR 0033).
                info["dead"].append(summary["path"])
            elif complete > recovery:
                info["finished_after"].append(summary["path"])
        if started and started < loss and (complete is None or complete < started):
            unfinished = True
            if activity and activity > recovery:
                wrote_after = True
    reasons.append(f"rollouts={len(info['rollouts'])}")
    reasons.append(f"task_complete_in_outage={len(info['dead'])}")
    if info["dead"]:
        if pane_class not in ("network_error", UNREADABLE):
            return "skip", reasons + ["pane_not_network_error"], info
        if pane_class == UNREADABLE:
            # Host cannot expose pane text (Ghostty); the impossible task_complete decides.
            return "resume", reasons + ["two_signals_agree"], info
        return "resume", reasons + ["all_three_agree"], info
    if unfinished and pane_class == "other":
        if wrote_after:
            return "skip", reasons + ["alive_after_recovery"], info
        if (now - recovery).total_seconds() < GRACE_SECS:
            return "skip", reasons + ["grace_pending"], info
        if screen and is_pane(screen):
            return "resume", reasons + ["idle_unfinished_turn"], info
    if pane_class not in ("network_error", UNREADABLE):
        return "skip", reasons + ["pane_not_network_error"], info
    if info["finished_after"]:
        return "skip", reasons + ["finished_after_recovery"], info
    return "skip", reasons + ["no_task_complete_in_outage"], info
