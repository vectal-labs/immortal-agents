#!/usr/bin/env python3
"""Single host-and-detector revive loop called by the frozen watcher trigger."""

from __future__ import annotations

import json
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
from immortal.core import bb_recovery
from immortal.core import ready
from immortal.core import telemetry
from immortal.core.common import iso_after, now_iso, parse_ts
from immortal.core.logbook import log, save_state
from immortal.core.revive_state import (
    MAX_REVIVES, error_at, mark_seen, target_key,
    attempt_allowed, reserve, delivered,
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


def _signature(info, target):
    return error_at(info) or str(target.get("error_identity") or json.dumps(info, sort_keys=True, default=str))


def _resume(state, host, target, harness, duration, mode, info, key):
    ref = target["ref"]
    attempt_id = outcomes.track(state, host.NAME, ref, harness, info, host_bb._thread_events)
    # Observation and reservation must both survive a crash after host acceptance.
    previous = state.setdefault("revived", {}).get(key)
    try:
        reserve(state, key, now_iso(datetime.now(timezone.utc)),
                _signature(info, target), mode, attempt_id,
                target_identity=info.get("path") or f"{host.NAME}:{ref}")
    except Exception:
        # No external send happened. Do not persist a phantom unknown delivery
        # if the outer watcher later succeeds in saving this in-memory state.
        outcomes.cancel(state, attempt_id)
        if previous is None:
            state["revived"].pop(key, None)
        else:
            state["revived"][key] = previous
        raise
    try:
        delivery = host.resume(target)
    except Exception as exc:
        log("resume_error", host=host.NAME, ref=ref, error=str(exc))
        telemetry.exception(exc)
        delivery = "unknown"
    if delivery not in ("sent", "queued", "not_sent", "unknown", "superseded"):
        delivery = "unknown"
    if delivery in ("not_sent", "superseded"):
        outcomes.cancel(state, attempt_id)
    delivered(state, key, delivery)
    ok = True if delivery in ("sent", "queued") else None if delivery == "unknown" else False
    log("resume_sent", host=host.NAME, ref=ref, ok=ok, delivery=delivery)
    trigger = "provider_outage" if mode == "provider" else None
    telemetry.send("revive_attempt", attempt_id=attempt_id, harness=harness,
                   host=host.NAME, result=ok, error=trigger or "network_outage")
    if delivery != "superseded":
        announce(host.NAME, harness, duration, target.get("title") or target.get("cwd") or ref, ok,
                 detail=info.get("error_detail") if trigger else None, trigger=trigger)
    return int(delivery in ("sent", "queued"))


def _fresh_terminal(host, target, detector, info, window):
    current = next((item for item in host.list_targets() if isinstance(item, dict) and item.get("ref") == target["ref"]), None)
    if current is None or any(current.get(k) != target.get(k) for k in ("id", "cwd", "harness_hint")):
        return None
    if target.get("recovery_at"):
        current["recovery_at"] = target["recovery_at"]
    screen = host.read_screen(current["ref"])
    name, _ = detect_harness(screen, current.get("title"), current.get("harness_hint"))
    if name != detector.NAME:
        return None
    decision, _, fresh_info = detector.evaluate(current, screen, window)
    if decision != "resume" or _signature(fresh_info, current) != _signature(info, target):
        return None
    return current


def _recover_target(state, host, target, window, mode, duration):
    ref = target["ref"]
    screen = host.read_screen(ref)
    if host.NAME == "bb" and (target.get("submission") or target.get("interruption")):
        if mode != "provider":
            return 0
        if target.get("interruption"):
            return bb_recovery.recover_interruption(state, target, parse_ts(window[1]), announce)
        return bb_recovery.recover(state, target, screen, parse_ts(window[1]), announce)
    name, reason = ((host.DETECTOR, "host_detector") if host.DETECTOR else
                    detect_harness(screen, target.get("title"), target.get("harness_hint")))
    harness = target.get("harness_hint") if host.DETECTOR else name
    log("harness_detected", host=host.NAME, ref=ref, harness=harness, reason=reason)
    if not name:
        log("decision", host=host.NAME, ref=ref, decision="skip", reasons=[reason], mode=mode)
        return 0
    detector = bb_provider_detect if mode == "provider" else DETECTORS[name]
    if state.get("last_api_ready_at"):
        target["recovery_at"] = state["last_api_ready_at"]
    decision, reasons, info = detector.evaluate(target, screen, window)
    info = info or {}
    key = target_key(host, target, info, window, mode)
    entry = state.setdefault("revived", {}).get(key, {})
    log("decision", host=host.NAME, ref=ref, harness=harness, decision=decision,
        reasons=reasons, info=info, tries=entry.get("tries", 0), mode=mode)
    if "silent_hang_observed" in reasons:
        log("silent_hang_observed", host=host.NAME, ref=ref, acted=False, reasons=reasons)
    if decision == "unknown":
        identity = _signature(info, target)
        if entry.get("seen_error") != identity:
            mark_seen(state, key, info.get("error_at"))
            state["revived"][key]["seen_error"] = identity
            save_state(state)
            announce(host.NAME, harness, duration, target.get("title") or ref, None,
                     detail=info.get("error_detail") or "bb reported an error without provider details",
                     trigger="unhandled_provider_error")
        return 0
    if decision != "resume" or not attempt_allowed(
        state, key, mode, _signature(info, target), datetime.now(timezone.utc),
        target_identity=info.get("path") or f"{host.NAME}:{ref}",
    ):
        return 0
    if target.get("bb_retry"):
        info["bb_retry"] = target["bb_retry"]
    # bb performs the fresh check inside its guarded host action.
    if host.NAME != "bb":
        target = _fresh_terminal(host, target, detector, info, window)
        if target is None:
            log("decision", host=host.NAME, ref=ref, decision="skip", reasons=["target_changed"], mode=mode)
            return 0
    return _resume(state, host, target, harness, duration, mode, info, key)


def revive_pass(state, window, mode):
    """A failed host or target cannot prevent the others from recovering."""
    if mode == "first":
        telemetry.send("outage_detected")
    sent = 0
    duration = _offline_duration(state, window, mode)
    for host in ((host_bb,) if mode == "provider" else HOSTS):
        try:
            if not host.available():
                log("host_skipped", host=host.NAME, reason="not_running")
                continue
            targets = host.list_targets()
            if not isinstance(targets, (list, tuple)):
                raise TypeError("host targets must be a list")
        except Exception as exc:
            log("host_error", host=host.NAME, error=str(exc))
            telemetry.exception(exc)
            continue
        log("targets_enumerated", host=host.NAME, count=len(targets))
        for target in targets:
            try:
                sent += _recover_target(state, host, target, window, mode, duration)
            except Exception as exc:
                log("target_error", host=host.NAME, ref=target.get("ref") if isinstance(target, dict) else None, error=str(exc))
                telemetry.exception(exc)
    return sent


def queue_recovery(state, loss_at, recovery_at, duration):
    """Persist the outage before any external call; later online ticks drain it."""
    pending = state.get("pending_recovery") or {}
    state["pending_recovery"] = {
        "loss_at": pending.get("loss_at") or loss_at,
        "probe_recovery_at": recovery_at,
        "duration": max(pending.get("duration", 0), duration),
    }
    state["recheck"] = None
    state["last_api_ready_at"] = None
    save_state(state)
    ready.invalidate()


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
    """Advance pending recovery without waiting for DNS in the watcher loop."""
    pending = state.get("pending_recovery")
    rc = state.get("recheck")
    if not pending and not rc:
        return
    now = datetime.now(timezone.utc)
    if not pending:
        if now > parse_ts(rc["until"]):
            state["recheck"] = None
            log("recheck_done", loss_at=rc["loss_at"])
            save_state(state)
            return
        if now < parse_ts(rc["next_at"]):
            return
    if not ready.check():
        return
    if pending:
        at = now_iso(now)
        state["last_api_ready_at"] = state.get("last_api_ready_at") or at
        # Keep the first pass pending across crashes, even beyond the normal
        # recheck deadline. Saved per-target reservations prevent duplicate sends.
        save_state(state)
        log("outage_window", loss_at=pending["loss_at"],
            probe_recovery_at=pending["probe_recovery_at"], api_ready_at=state["last_api_ready_at"])
        revive_pass(state, (pending["loss_at"], at), "first")
        arm_recheck(state, pending["loss_at"], pending["duration"])
        state["pending_recovery"] = None
        save_state(state)
    else:
        # Save the next tick first. An external failure cannot make us spin.
        rc["next_at"] = iso_after(now, RECHECK_SECS)
        save_state(state)
        log("recheck", loss_at=rc["loss_at"])
        if revive_pass(state, (rc["loss_at"], now_iso(now)), "recheck"):
            arm_recheck(state, rc["loss_at"], rc["duration"])
            save_state(state)


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
    if not ready.check():
        return
    stamp = now_iso(now)
    revive_pass(state, (stamp, stamp), "provider")
