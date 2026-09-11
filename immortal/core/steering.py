"""ADR 0052: after a reconnect or wake, steer every still-active BB thread once.

An episode opens when the watcher comes back online or notices that the Mac
slept. Its candidates are the threads active at that moment; each gets one
"keep going" once its provider answers again. The episode is saved before any
send, so polls and watcher restarts cannot repeat it.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from immortal.core import ready
from immortal.core.common import iso_after, now_iso, parse_ts
from immortal.core.logbook import log, save_state
from immortal.hosts import bb as host_bb

# A wake usually shows as a tick gap and then a reconnect seconds later; that
# reconnect joins the wake episode once. Any other reconnect is a new episode.
EPISODE_MERGE_SECS = 60
# time.monotonic() is mach_absolute_time() on macOS: it stops while the Mac
# sleeps, so wall time minus monotonic time is sleep, never a slow scan. A
# 10-second lid close counts; the tolerance only absorbs clock jitter.
WAKE_GAP_SECS = 5
# Providers that never come back within this window get no stale nudge.
EPISODE_WINDOW_SECS = 900
_last_mono = None


def _slept(steer, now, mono):
    last_tick = parse_ts(steer.get("last_tick_at"))
    if not last_tick or _last_mono is None:
        return 0  # first tick of this process: a restart is not a wake
    return (now - last_tick).total_seconds() - (mono - _last_mono)


def _triggers(state, steer, now, mono):
    reasons = []
    if _slept(steer, now, mono) > WAKE_GAP_SECS:
        reasons.append("wake")
    recovery = state.get("last_recovery_at")
    if recovery and recovery != steer.get("seen_recovery_at"):
        steer["seen_recovery_at"] = recovery
        reasons.append("reconnect")
    return reasons


def _candidates(state, since):
    """Active threads right now, minus ones ordinary recovery retried since this
    episode's tick began. Earlier unconfirmed attempts have no time limit and do not count.
    None when bb cannot answer yet (asleep, restarting): the snapshot is retried."""
    if not host_bb.available():
        return None
    try:
        active = host_bb.list_active()
    except host_bb.BbUnavailable:
        return None
    retried = {attempt.get("ref") for attempt in (state.get("pending_revives") or {}).values()
               if isinstance(attempt, dict) and attempt.get("host") == host_bb.NAME
               and (parse_ts(attempt.get("sent_at")) or since) >= since}
    return {target["ref"]: {"harness": target.get("harness_hint"), "title": target.get("title"),
                            "endpoint": _endpoint(target)}
            for target in active if target["ref"] not in retried}


def _endpoint(target):
    """A failed thread-log read costs that thread its route, not the others their nudge."""
    try:
        return host_bb.recovery_endpoint(target)
    except Exception as exc:
        log("steer_endpoint_unknown", ref=target["ref"], error=str(exc))
        return None


def _open_episode(state, steer, reasons, now):
    episode = steer.get("episode")
    opened = parse_ts(episode.get("at")) if isinstance(episode, dict) else None
    if opened and episode.get("reasons") == ["wake"] and (now - opened).total_seconds() <= EPISODE_MERGE_SECS:
        episode["reasons"] = episode["reasons"] + reasons
        log("steer_episode", action="merged", at=episode["at"], reasons=episode["reasons"])
        return
    # The watcher stamps last_probe_at before ordinary recovery runs in this
    # tick, so attempts from that stamp on belong to this reconnect.
    steer["episode"] = {"at": now_iso(now), "until": iso_after(now, EPISODE_WINDOW_SECS),
                        "since": state.get("last_probe_at") or now_iso(now),
                        "reasons": reasons, "candidates": None, "sent": {}}
    log("steer_episode", action="opened", at=steer["episode"]["at"], reasons=reasons)
    _snapshot(state, steer["episode"])


def _snapshot(state, episode):
    """Freeze the active set once; later work is not part of this reconnect."""
    found = _candidates(state, parse_ts(episode.get("since")) or parse_ts(episode["at"]))
    if found is None:
        log("steer_episode", action="snapshot_deferred", at=episode["at"])
        return False
    episode["candidates"] = found
    log("steer_episode", action="snapshot", at=episode["at"], candidates=sorted(found))
    return True


def tick(state, now=None):
    global _last_mono
    now = now or datetime.now(timezone.utc)
    mono = time.monotonic()
    steer = state.get("steer")
    if not isinstance(steer, dict):
        steer = state["steer"] = {}
    if "seen_recovery_at" not in steer:
        steer["seen_recovery_at"] = state.get("last_recovery_at")  # first run: an old reconnect is not news
    online = state.get("online") is True
    reasons = _triggers(state, steer, now, mono) if online else []
    steer["last_tick_at"], _last_mono = now_iso(now), mono
    if reasons:
        # Results cached before the outage or sleep say nothing about now.
        ready.invalidate()
        ready.close_endpoints()
        _open_episode(state, steer, reasons, now)
        save_state(state)
    episode = steer.get("episode")
    if not isinstance(episode, dict) or not isinstance(episode.get("sent"), dict):
        return
    if now > (parse_ts(episode.get("until")) or now):
        log("steer_episode", action="expired", at=episode.get("at"), sent=len(episode["sent"]))
        steer["episode"] = None
        save_state(state)
        return
    if not online:
        return
    if not isinstance(episode.get("candidates"), dict):
        if not _snapshot(state, episode):
            return
        save_state(state)
    _steer_candidates(state, episode, now)


def _ready(candidate):
    if candidate.get("endpoint"):
        return ready.check_endpoint(candidate["endpoint"]) == "reachable"
    return ready.check()  # unknown route: the legacy DNS readiness gate


def _steer_candidates(state, episode, now):
    for ref, candidate in episode["candidates"].items():
        if ref in episode["sent"] or not _ready(candidate):
            continue
        # Reserve first: a crash after bb accepts the steer must not resend.
        episode["sent"][ref] = {"at": now_iso(now), "delivery": "unknown"}
        save_state(state)
        try:
            delivery = host_bb.steer({"ref": ref, **candidate})
        except Exception as exc:  # one thread must not stop the others
            log("steer_error", ref=ref, error=str(exc))
            delivery = "unknown"
        if delivery == "not_sent":
            episode["sent"].pop(ref, None)  # nothing reached bb; retry next tick
        else:
            episode["sent"][ref]["delivery"] = delivery
        save_state(state)
        log("steer_sent", ref=ref, harness=candidate.get("harness"), title=candidate.get("title"),
            delivery=delivery, reasons=episode.get("reasons"))
