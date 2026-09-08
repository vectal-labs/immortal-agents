"""Bounded recovery of rejected bb starts, independent of internet outage length."""

from immortal.core.common import iso_after, now_iso, parse_ts
from immortal.core.logbook import log, save_state
from immortal.core import outcomes, telemetry
from immortal.core.revive_state import MAX_REVIVES, PROVIDER_EPISODE_SECS
from immortal.hosts import bb

RETRY_DELAYS = (30, 60, 120)
MAX_AGE_SECS = 30 * 60
TIMEOUTS = ("JSON-RPC request timed out: thread/resume", "JSON-RPC request timed out: turn/start")


def recover_interruption(state, target, now, announce):
    failure = target["interruption"]
    ref = target["ref"]
    key = f"bb:{ref}:daemon"
    entry = state.setdefault("revived", {}).setdefault(key, {"tries": 0})
    error_at = parse_ts(target.get("error_at"))

    def decision(reason):
        log("decision", host="bb", ref=ref, mode="daemon", reason=reason, tries=entry["tries"])

    def alert(reason):
        entry["handled_seq"] = failure["error_seq"]
        save_state(state)
        announce("bb", target.get("harness_hint"), 0, target.get("title") or ref, False,
                 detail=reason, trigger="bb_daemon_interruption")

    if failure["error_seq"] <= entry.get("handled_seq", -1):
        decision("interruption_already_handled")
        return 0
    if not error_at or (now - error_at).total_seconds() > MAX_AGE_SECS:
        decision("interruption_too_old")
        return 0
    if (now - error_at).total_seconds() < RETRY_DELAYS[0]:
        decision("interruption_retry_delay")
        return 0
    recent = [stamp for stamp in entry.get("attempts", [])
              if parse_ts(stamp) and (now - parse_ts(stamp)).total_seconds() < PROVIDER_EPISODE_SECS]
    if len(recent) >= MAX_REVIVES:
        alert("daemon_recovery_hourly_limit")
        return 0
    try:
        if not bb.submission_ready(target):
            decision("interruption_not_ready_or_superseded")
            return 0
    except (bb.BbUnavailable, bb.subprocess.TimeoutExpired, OSError, ValueError, KeyError):
        decision("interruption_status_unavailable")
        return 0
    info = {"label": target.get("title") or ref,
            "bb_retry": {"original_request_id": failure["original_request_id"],
                         "attempt": failure["attempt"] + 1},
            "bb_interruption": {"label": target.get("title") or ref}}
    attempt_id = outcomes.track(state, "bb", ref, target.get("harness_hint"), info, bb._thread_events)
    entry.update(handled_seq=failure["error_seq"], tries=len(recent) + 1,
                 attempts=[*recent, now_iso(now)], delivery="unknown")
    save_state(state)
    delivery = bb.retry_submission(target)
    entry["delivery"] = delivery
    if delivery not in ("sent", "queued", "unknown"):
        outcomes.cancel(state, attempt_id)
    save_state(state)
    decision(f"interruption_retry_{delivery}")
    telemetry.send("revive_attempt", attempt_id=attempt_id, harness=target.get("harness_hint"),
                   host="bb", result=delivery in ("sent", "queued"), error="bb_daemon_interruption")
    if delivery in ("sent", "queued"):
        announce("bb", target.get("harness_hint"), 0, target.get("title") or ref, True,
                 trigger="bb_daemon_resume_queued" if delivery == "queued" else "bb_daemon_interruption")
    elif delivery == "command_failed":
        alert("daemon_recovery_command_failed")
    return int(delivery in ("sent", "queued"))


def recover(state, target, detail, now, announce):
    failure = target["submission"]
    ref, request = target["ref"], failure["request_id"]
    key = f"bb:{ref}:submission:{failure['original_request_id']}"
    entry = state.setdefault("revived", {}).setdefault(key, {"tries": 0})
    error_at = parse_ts(target.get("error_at"))

    def decision(reason):
        log("decision", host="bb", ref=ref, mode="submission", request_id=request,
            reason=reason, tries=entry["tries"])

    def alert(reason):
        decision(reason)
        if entry.get("alerted") == reason:
            return
        entry["alerted"] = reason
        save_state(state)
        announce("bb", target.get("harness_hint"), 0, target.get("title") or ref, False,
                 detail=reason, trigger="submission_recovery_failed")

    if not error_at or detail not in TIMEOUTS:
        alert("unhandled_submission_error")
        return 0
    if (now - error_at).total_seconds() > MAX_AGE_SECS:
        decision("submission_too_old")
        return 0
    if entry["tries"] >= len(RETRY_DELAYS):
        alert("submission_retries_exhausted")
        return 0
    # The same failed event after a send may mean a reply was lost, or a queued
    # retry was cancelled. Never send that event twice, including after restart.
    signature = f"{request}:{failure['error_seq']}"
    if entry.get("attempted_error") == signature:
        if entry.get("delivery") in ("unknown", "command_failed", "reset_failed"):
            alert("submission_retry_unconfirmed")
        else:
            decision("submission_retry_pending")
        return 0
    due = max(error_at, parse_ts(entry.get("at")) or error_at)
    due = parse_ts(iso_after(due, RETRY_DELAYS[entry["tries"]]))
    if entry.get("next_at") != now_iso(due):
        entry["next_at"] = now_iso(due)
        save_state(state)
    if now < due:
        decision("submission_retry_delay")
        return 0
    try:
        if not bb.submission_ready(target):
            decision("submission_superseded_or_queued")
            return 0
    except (bb.BbUnavailable, bb.subprocess.TimeoutExpired, OSError, ValueError, KeyError):
        decision("submission_status_unavailable")
        return 0

    # Reserve before dispatch: process crashes must not reset the retry budget.
    entry.update(tries=entry["tries"] + 1, at=now_iso(now), attempted_error=signature,
                 delivery="unknown")
    info = {"label": target.get("title") or ref,
            "error_at": target["error_at"], "error_detail": detail,
            "bb_retry": {"original_request_id": failure["original_request_id"],
                         "attempt": failure["attempt"] + 1}}
    attempt_id = outcomes.track(state, "bb", ref, target.get("harness_hint"), info, bb._thread_events)
    save_state(state)
    delivery = bb.retry_submission(target, reset=entry["tries"] == 2)
    entry["delivery"] = delivery
    if delivery not in ("sent", "queued", "unknown"):
        outcomes.cancel(state, attempt_id)
    save_state(state)
    telemetry.send("revive_attempt", attempt_id=attempt_id, harness=target.get("harness_hint"),
                   host="bb", result=delivery in ("sent", "queued"), error="submission_timeout")
    decision(f"submission_retry_{delivery}")
    return int(delivery in ("sent", "queued"))
