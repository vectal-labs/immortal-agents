#!/usr/bin/env python3
"""Single host-and-detector revive loop called by the frozen watcher trigger."""

from __future__ import annotations

from datetime import datetime, timezone

from immortal.detect import bb as bb_detect
from immortal.detect import bb_provider as bb_provider_detect
from immortal.detect import claude as claude_detect
from immortal.detect import codex as codex_detect
from immortal.detect import pi as pi_detect
from immortal.hosts import bb as host_bb
from immortal.hosts import cmux as host_cmux
from immortal.hosts import ghostty as host_ghostty
from immortal.hosts import terminal as host_terminal
from immortal.core import notify
from immortal.core import outcomes
from immortal.core import ready
from immortal.core import telemetry
from immortal.core.common import iso_after, now_iso, parse_ts
from immortal.core.logbook import log
from immortal.core.revive_state import (
    MAX_REVIVES, error_at, mark_revived, mark_seen, may_revive, target_key,
)

# Experiment 0009: a revive can die again (stale DNS). ADR 0051: also wait
# out the Codex liveness grace after any qualifying outage, even if the
# first pass revived nothing. Window must cover GRACE_SECS (600).
RECHECK_SECS = 60
RECHECK_WINDOW_SECS = 900
# Provider-side outages while online (Grok "at capacity", 2026-09-03): poll bb
# every 30s. A thread gets at most MAX_REVIVES such revives per hour.
PROVIDER_CHECK_SECS = 30
HOSTS = (host_cmux, host_terminal, host_ghostty, host_bb)
DETECTORS = {
    detector.NAME: detector
    for detector in (claude_detect, codex_detect, pi_detect, bb_detect)
}


def detect_harness(screen, title, hint=None):
    """Identify a harness from screen text, a process hint, then the title."""
    harness_detectors = {name: detector for name, detector in DETECTORS.items() if name != "bb"}
    hits = [name for name, detector in harness_detectors.items() if detector.is_pane(screen or "")]
    if len(hits) == 1:
        return hits[0], "pane_text"
    if len(hits) > 1:
        return None, "ambiguous_pane_text"
    if hint in harness_detectors:
        return hint, "process"
    raw_title = title or ""
    lowered = raw_title.lower()
    titled = []
    for name, detector in harness_detectors.items():
        name_in_title = name in ("claude", "codex") and name in lowered
        pi_title = name == "pi" and raw_title.strip().lower() in ("π", "pi")
        if detector.is_pane(raw_title) or name_in_title or pi_title:
            titled.append(name)
    if len(titled) == 1:
        return titled[0], "surface_title"
    if len(titled) > 1:
        return None, "ambiguous_pane_text"
    return None, "unknown_harness"


def _offline_duration(state, window, mode):
    if mode == "recheck":
        return state.get("recheck", {}).get("duration", 0)
    elif mode in ("first", "provider"):
        end = state.get("last_recovery_at") or window[1]
        loss_at, recovery_at = parse_ts(window[0]), parse_ts(end)
        return (recovery_at - loss_at).total_seconds() if loss_at and recovery_at else 0
    raise ValueError(f"unknown revive mode: {mode}")


def _resume(state, host, target, harness, duration, mode, info):
    ref = target["ref"]
    attempt_id = outcomes.track(state, host.NAME, ref, harness, info, host_bb._thread_events)
    try:
        ok = host.resume(ref)
    except Exception:
        outcomes.cancel(state, attempt_id)
        raise
    if not ok:
        outcomes.cancel(state, attempt_id)
    log("resume_sent", host=host.NAME, ref=ref, ok=ok)
    label = target.get("title") or target.get("cwd") or ref
    trigger = "provider_outage" if mode == "provider" else None
    announce(host.NAME, harness, duration, label, ok,
             detail=info.get("error_detail") if trigger else None, trigger=trigger)
    telemetry.send("revive_attempt", attempt_id=attempt_id, harness=harness,
                   host=host.NAME, result=ok, error=trigger or "network_outage")
    return ok


def revive_pass(state, window, mode):
    """Run one detector-and-revive loop over the hosts selected by mode. Crashes are reported, then re-raised."""
    try:
        mode == "first" and telemetry.send("outage_detected")
        return _revive_pass(state, window, mode)
    except Exception as exc:
        telemetry.exception(exc)
        raise


def _revive_pass(state, window, mode):
    sent = 0
    hosts = (host_bb,) if mode == "provider" else HOSTS
    duration = _offline_duration(state, window, mode)
    for host in hosts:
        if not host.available():
            log("host_skipped", host=host.NAME, reason="not_running")
            continue
        targets = host.list_targets()
        log("targets_enumerated", host=host.NAME, count=len(targets), targets=targets)
        for target in targets:
            ref = target["ref"]
            screen = host.read_screen(ref)
            name, reason = (
                (host.DETECTOR, "host_detector")
                if host.DETECTOR
                else detect_harness(
                    screen, target.get("title"), target.get("harness_hint")
                )
            )
            harness = target["harness_hint"] if host.DETECTOR else name
            log("harness_detected", host=host.NAME, ref=ref, title=target.get("title"),
                harness=harness, reason=reason)
            if not name:
                log("decision", host=host.NAME, ref=ref, harness=None,
                    cwd=target.get("cwd"), title=target.get("title"), decision="skip",
                    reasons=[reason], info={}, tries=0, mode=mode)
                continue
            detector = bb_provider_detect if mode == "provider" else DETECTORS[name]
            if state.get("last_api_ready_at"):
                target["recovery_at"] = state["last_api_ready_at"]
            decision, reasons, info = detector.evaluate(target, screen, window)
            log("eval", host=host.NAME, ref=ref, harness=harness,
                cwd=target.get("cwd"), info=info)
            key = target_key(host, target, info, window, mode)
            entry = state.setdefault("revived", {}).get(key, {})
            if mode == "provider" and info.get("error_at") == entry.get("error_at"):
                continue
            log("decision", host=host.NAME, ref=ref, harness=harness,
                cwd=target.get("cwd"), title=target.get("title"), decision=decision,
                reasons=reasons, info=info, tries=entry.get("tries", 0), mode=mode)
            if "silent_hang_observed" in reasons:
                log("silent_hang_observed", host=host.NAME, ref=ref,
                    cwd=target.get("cwd"), acted=False, reasons=reasons)
            if decision == "unknown":
                mark_seen(state, key, info["error_at"])
                label = target.get("title") or ref
                announce(host.NAME, harness, duration, label, None,
                         detail=info["error_detail"], trigger="unhandled_provider_error")
                continue
            if decision != "resume":
                continue
            if not may_revive(state, key, mode):
                if mode == "provider":
                    log("provider_revive_capped", thread=ref, tries=entry.get("tries", 0))
                    mark_seen(state, key, info["error_at"])
                continue
            ok = _resume(state, host, target, harness, duration, mode, info)
            if ok:
                sent += 1
            if ok or mode == "provider":
                mark_revived(state, key, window[1], error_at(info), mode)
    return sent


def arm_recheck(state, loss_at, duration):
    now = datetime.now(timezone.utc)
    state["recheck"] = {
        "loss_at": loss_at,
        "duration": duration,
        "next_at": iso_after(now, RECHECK_SECS),
        "until": iso_after(now, RECHECK_WINDOW_SECS),
    }
    log("recheck_armed", **state["recheck"])


def run_recheck(state):
    """Called every online tick. Re-runs the pass on revived targets; the
    outage window is stretched to now so an error after the revive counts."""
    rc = state.get("recheck")
    if not rc:
        return
    now = datetime.now(timezone.utc)
    if now < parse_ts(rc["next_at"]):
        return
    if now > parse_ts(rc["until"]):
        state["recheck"] = None
        log("recheck_done", loss_at=rc["loss_at"])
        return
    log("recheck", loss_at=rc["loss_at"])
    ready.wait_for_apis()
    if revive_pass(state, (rc["loss_at"], now_iso()), "recheck"):
        arm_recheck(state, rc["loss_at"], rc["duration"])
    else:
        rc["next_at"] = iso_after(now, RECHECK_SECS)


def announce(host, harness, offline_secs, label, ok, detail=None, trigger=None):
    """Discord ping per revive attempt. Failure to notify never blocks recovery."""
    sent = notify.notify_revive(host, harness, offline_secs, label, ok, detail, trigger)
    log("notify", host=host, harness=harness, ok=ok, sent=sent)


def run_provider_check(state):
    """Run the provider-mode pass when its polling interval has elapsed."""
    now = datetime.now(timezone.utc)
    next_at = state.get("provider_check_next_at")
    if next_at and now < parse_ts(next_at):
        return
    state["provider_check_next_at"] = iso_after(now, PROVIDER_CHECK_SECS)
    outcomes.check(state, host_bb._thread_events, now)
    telemetry.heartbeat()
    stamp = now_iso(now)
    revive_pass(state, (stamp, stamp), "provider")
