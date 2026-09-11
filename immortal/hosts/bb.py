"""bb host adapter implementing the shared host contract via the bb CLI.

Targets are in-scope dead threads and harness_hint is their providerId.
list_targets() fetches and caches each thread's final error.
read_screen() returns the cached detail without another bb call.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from immortal.core.bb_runtime import DEFAULT_BB, BbRuntimeError, available as runtime_available, bb_cmd, run_bb
from immortal.core.common import RESUME_TEXT
from immortal.core.logbook import log

NAME = "bb"
DETECTOR = "bb"

# launchd's PATH has no bb, so default to the app bundle path (like CMUX).
BB = os.environ.get("BB_BIN") or os.environ.get("BB_CLI") or DEFAULT_BB
PROVIDERS = ("claude-code", "codex", "pi", "acp-cursor")
# ADR 0048: Cursor (acp) in bb is in scope, but it never reports a failure
# structurally: no provider/error, turn/completed says "completed" and the
# thread goes idle. The only trace is a final agent message that is just the
# CLI error line ("Error: RetriableError: Connection stalled", thr_vp7hipzyr6,
# 2026-09-03). Recent idle threads of these providers are candidates too.
MESSAGE_ERROR_PROVIDERS = ("acp-cursor",)
IDLE_LOOKBACK_SECS = 24 * 3600
_ERRORS = {}
# thread id -> (updatedAt, error): `bb thread log` is skipped for unchanged
# threads, so scanning idle candidates every 30s stays cheap.
_LOG_CACHE = {}


class BbUnavailable(Exception):
    """bb binary missing or the bb server is not answering."""


def _ms_to_dt(ms):
    if not isinstance(ms, (int, float)) or isinstance(ms, bool):
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _rows(value):
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise BbUnavailable("bb returned an unexpected list shape")
    return value


def _object(value):
    if not isinstance(value, dict):
        raise BbUnavailable("bb returned an unexpected object shape")
    return value


def bb_json(args):
    try:
        proc = run_bb([*args, "--json"])
    except BbRuntimeError as exc:
        raise BbUnavailable(str(exc))
    if proc.returncode != 0:
        raise BbUnavailable(f"bb {' '.join(args)} failed: {proc.stderr.strip()[-300:]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise BbUnavailable(f"bb {' '.join(args)} returned non-JSON: {exc}")


def available():
    return runtime_available()


def _list_error_threads():
    """Threads that may be dead: error status, or recent idle message-error ones."""
    threads = bb_json(["thread", "list"])
    if not isinstance(threads, list):
        raise BbUnavailable("bb returned an unexpected thread list shape")
    cutoff_ms = (datetime.now(timezone.utc).timestamp() - IDLE_LOOKBACK_SECS) * 1000
    return [
        t
        for t in threads
        if isinstance(t, dict) and t.get("providerId") in PROVIDERS
        and not t.get("archivedAt")  # bb thread list includes archived threads
        and not t.get("deletedAt")
        and isinstance(t.get("id"), str) and t["id"]
        and (
            t.get("status") == "error"
            or (
                t.get("providerId") in MESSAGE_ERROR_PROVIDERS
                and t.get("status") == "idle"
                and isinstance(t.get("latestAttentionAt"), (int, float))
                and (t.get("latestAttentionAt") or 0) >= cutoff_ms
            )
        )
    ]


def _thread_events(thread_id):
    return _rows(bb_json(["thread", "log", thread_id, "--all"]))


def _message_error(item):
    """A message-provider failure is a final agent message that is only the error line."""
    text = item.get("text") or ""
    if not isinstance(text, str):
        raise BbUnavailable("bb returned non-text agent output")
    text = text.strip()
    if item.get("type") == "agentMessage" and text.lower().startswith("error:") and "\n" not in text:
        return text
    return None


def last_error(events, provider=None):
    """The last turn's rejected submission or final provider error.
    For MESSAGE_ERROR_PROVIDERS the last agent message decides instead."""
    final = None
    request = None
    request_seq = None
    active_turn = interrupted = restart_error = None
    accepted = False
    for ev in _rows(events):
        data = _object(ev.get("data", {}))
        kind = ev.get("type")
        turn = _object(ev.get("scope", {})).get("turnId")
        if kind == "client/turn/requested":
            if "requestId" in data and (not isinstance(data["requestId"], str) or not data["requestId"]):
                raise BbUnavailable("bb returned invalid request identity")
            request, final = data, None
            request_seq = ev.get("seq")
            active_turn = interrupted = restart_error = None
            accepted = False
        elif kind == "client/turn/rejected":
            if (request and data.get("requestId") == request.get("requestId")
                    and data.get("reason") == "command_failed"):
                final = {
                    "detail": data.get("message") or "", "at": _ms_to_dt(ev.get("createdAt")),
                    "submission": {
                        "request_id": request["requestId"],
                        "original_request_id": request.get("retryOfRequestId") or request["requestId"],
                        "attempt": request.get("retryAttempt", 1),
                        "error_seq": ev.get("seq"),
                    },
                }
        elif kind == "system/error" and data.get("code") == "thread_command_failed":
            # Pair the display error with its rejected request. A rename or an
            # unrelated command failure must never replay the last user input.
            if final and final.get("submission") and data.get("message") == "Command turn.submit failed":
                final.update(detail=data.get("detail") or final["detail"], at=_ms_to_dt(ev.get("createdAt")))
                final["submission"]["error_seq"] = ev.get("seq")
            if (interrupted == (turn, ev.get("createdAt"))
                    and data.get("message") == "Thread interrupted because the host daemon disconnected"):
                restart_error = ev
        elif kind == "turn/started":
            final = interrupted = restart_error = None
            active_turn, accepted = turn, False
        elif kind == "turn/input/accepted" and request:
            if data.get("clientRequestId") == request.get("requestId"):
                accepted = bool(turn and turn == active_turn)
                if final and final.get("submission"):
                    final = None
        elif kind == "turn/completed" and data.get("status") in ("completed", "interrupted"):
            interrupted = ((turn, ev.get("createdAt")) if accepted and turn == active_turn
                           and data.get("status") == "interrupted" else None)
            restart_error = None
            if provider not in MESSAGE_ERROR_PROVIDERS or data.get("status") == "interrupted":
                final = None
        elif kind == "system/thread/interrupted":
            final = None
            if (data.get("reason") == "host-daemon-restarted" and request
                    and request.get("requestId") and interrupted and restart_error
                    and interrupted[1] == ev.get("createdAt") and isinstance(ev.get("seq"), int)):
                final = {"detail": "BB daemon interruption", "at": _ms_to_dt(ev.get("createdAt")),
                         "interruption": {
                             "request_id": request["requestId"],
                             "original_request_id": request.get("retryOfRequestId") or request["requestId"],
                             "attempt": request.get("retryAttempt", 1),
                             "turn_id": interrupted[0], "error_seq": ev["seq"],
                         }}
            interrupted = restart_error = None
        elif kind == "provider/error":
            if data.get("willRetry"):
                continue
            final = {"detail": data.get("detail") or data.get("message") or "",
                     "at": _ms_to_dt(ev.get("createdAt")), "error_seq": ev.get("seq")}
        elif provider in MESSAGE_ERROR_PROVIDERS and ev.get("type") == "item/completed":
            item = _object(data.get("item", {}))
            if item.get("type") == "agentMessage":
                text = _message_error(item)
                final = {"detail": text, "at": _ms_to_dt(ev.get("createdAt")),
                         "error_seq": ev.get("seq")} if text else None
    if final:
        if not isinstance(final["detail"], str):
            raise BbUnavailable("bb returned a non-text error")
        request_id = (request or {}).get("requestId")
        final.update(request_id=request_id, request_seq=request_seq,
                     original_request_id=(request or {}).get("retryOfRequestId") or request_id,
                     retry_attempt=(request or {}).get("retryAttempt", 1))
    return final


def _observation(thread, error):
    """Detached scalar values: later scans cannot change an earlier decision."""
    if not error:
        return None
    snapshot = {key: thread.get(key) for key in (
        "id", "providerId", "status", "updatedAt", "latestAttentionAt",
    )}
    snapshot.update(request_id=error.get("request_id"), request_seq=error.get("request_seq"),
                    error_seq=error.get("error_seq"),
                    original_request_id=error.get("original_request_id"),
                    retry_attempt=error.get("retry_attempt"),
                    error_at=error["at"].isoformat() if error["at"] else None,
                    error_hash=hashlib.sha256(error["detail"].encode()).hexdigest())
    if any(value is not None and not isinstance(value, (str, int, float, bool))
           for value in snapshot.values()):
        raise BbUnavailable("bb returned invalid failure identity")
    return snapshot


def _retry_observation(thread, error):
    if (not error or thread.get("status") != "error" or error.get("submission")
            or error.get("interruption") or not error.get("request_id")):
        return None
    request_id, original, attempt = error["request_id"], error.get("original_request_id"), error.get("retry_attempt")
    if (not isinstance(request_id, str) or not isinstance(original, str) or not original
            or not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1):
        raise BbUnavailable("bb returned invalid retry identity")
    return {"original_request_id": original, "attempt": attempt + 1}


def _final_error(thread):
    ref, updated = thread.get("id"), thread.get("updatedAt")
    cached = _LOG_CACHE.get(ref)
    if updated and cached and cached[0] == updated:
        return cached[1]
    error = last_error(_thread_events(ref), thread.get("providerId"))
    if updated:
        _LOG_CACHE[ref] = (updated, error)
    return error


def list_targets():
    """Return in-scope dead threads, including final-error timing metadata."""
    try:
        threads = _list_error_threads()
    except (BbUnavailable, OSError, subprocess.TimeoutExpired) as exc:
        _ERRORS.clear()
        log("host_unavailable", host=NAME, error=str(exc))
        return []
    errors, targets = {}, []
    for thread in threads:
        ref = thread.get("id")
        try:
            error = _final_error(thread)
            if thread.get("status") != "error" and not error:
                continue  # an idle candidate that simply finished its turn
            error_at = error["at"].isoformat() if error and error["at"] else None
            targets.append({
                "ref": ref,
                "id": ref,
                "cwd": None,
                "title": thread.get("title"),
                "harness_hint": thread.get("providerId"),
                "environment_host_id": thread.get("environmentHostId"),
                # The detectors' death signal is status == "error". A dead
                # Cursor thread is idle in bb, so the host reports the death.
                "status": "error",
                "bb_status": thread.get("status"),
                "error_at": error_at,
                "error_identity": thread.get("latestAttentionAt") or thread.get("updatedAt"),
                "submission": deepcopy(error.get("submission")) if error else None,
                "interruption": deepcopy(error.get("interruption")) if error else None,
                "bb_observation": _observation(thread, error),
                "bb_retry": _retry_observation(thread, error),
                "has_pending_interaction": thread.get("hasPendingInteraction", False),
            })
            errors[ref] = (error["detail"] if error else None, error_at)
        except (BbUnavailable, OSError, subprocess.TimeoutExpired, ValueError, TypeError, KeyError) as exc:
            log("target_unavailable", host=NAME, ref=ref, error=str(exc))
    _ERRORS.clear()
    _ERRORS.update(errors)
    return targets


def read_screen(ref):
    return _ERRORS.get(ref, (None, None))[0]


def _local_host_id():
    try:
        return (Path(os.environ.get("BB_DATA_DIR", Path.home() / ".bb")) / "host-id").read_text().strip()
    except OSError:
        return None


def recovery_endpoint(target):
    """Resolve a route from session/config evidence, never the harness name alone."""
    from immortal.core.provider_endpoint import recovery_endpoint as resolve
    local_host = _local_host_id()
    if not local_host or target.get("environment_host_id") != local_host:
        return None  # This Mac's connection says nothing about a remote agent.
    return resolve(target.get("harness_hint"), _thread_events(target["ref"]))


def _steerable(thread):
    """ADR 0052: a live turn that is not waiting on the user."""
    return (thread.get("providerId") in PROVIDERS and thread.get("status") == "active"
            and isinstance(thread.get("id"), str) and thread["id"]
            and not thread.get("archivedAt") and not thread.get("deletedAt")
            and not thread.get("hasPendingInteraction"))


def list_active():
    """Threads whose turn a reconnect steer may interrupt. Raises BbUnavailable, never guesses empty."""
    local_host = _local_host_id()
    if not local_host:
        return []
    try:
        threads = _rows(bb_json(["thread", "list"]))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BbUnavailable(str(exc))
    # This Mac's reconnect says nothing about an agent on another machine.
    return [{"ref": t["id"], "id": t["id"], "title": t.get("title"), "harness_hint": t.get("providerId"),
             "environment_host_id": local_host}
            for t in threads if isinstance(t, dict) and _steerable(t)
            and t.get("environmentHostId") == local_host]


def _waiting_on_user(ref):
    """`thread show` omits hasPendingInteraction; the interactions list is explicit."""
    return any(row.get("status", "pending") == "pending"
               for row in _rows(bb_json(["thread", "interactions", "list", ref])))


def steer(target):
    """Re-read the thread, then steer RESUME_TEXT into its live turn."""
    ref = target["ref"]
    try:
        current = _object(_object(bb_json(["thread", "show", ref])).get("thread"))
        if not _steerable(current) or _waiting_on_user(ref):
            return "superseded"
    except (BbUnavailable, BbRuntimeError, OSError, subprocess.TimeoutExpired, ValueError, TypeError, KeyError):
        return "not_sent"  # Read-only so far.
    try:
        proc = run_bb(["thread", "tell", ref, RESUME_TEXT, "--mode", "auto", "--json"])
    except BbRuntimeError:
        return "not_sent"
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return "unknown"
    log("bb_result", cmd=getattr(proc, "args", None) or ["thread", "tell", ref], code=proc.returncode,
        stdout=(proc.stdout or "")[-300:])
    if proc.returncode:
        return "unknown"  # A CLI error can arrive after bb accepted the input.
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return "unknown"
    if isinstance(data, dict) and data.get("ok") is True and data.get("delivery") in ("sent", "queued"):
        return data["delivery"]
    return "unknown"


def submission_ready(target):
    """Re-read bb immediately before touching a failed request."""
    ref = target["ref"]
    shown = _object(bb_json(["thread", "show", ref]))
    current = _object(shown.get("thread"))
    if target.get("interruption"):
        host_id = _object(shown.get("environment", {})).get("hostId")
        if not host_id or not any(machine.get("id") == host_id and machine.get("status") == "connected"
                                  for machine in _rows(bb_json(["machine", "list"]))):
            return False
    if (current.get("status") != "error" or current.get("archivedAt")
            or current.get("deletedAt") or current.get("activeBackgroundAgentCount", 0)
            or current.get("hasPendingInteraction") or target.get("has_pending_interaction")):
        return False
    if current.get("queuedMessageCount", 0):
        return False
    queued = _rows(bb_json(["thread", "queue", "list", ref]))
    if queued:
        return False
    if _rows(bb_json(["thread", "interactions", "list", ref])):
        return False
    error = last_error(_thread_events(ref), target.get("harness_hint"))
    failure_kind = "interruption" if target.get("interruption") else "submission"
    return bool(error and error.get(failure_kind) == target[failure_kind])


def retry_submission(target, reset=False):
    """Use bb's acceptance record and request guard; never reconstruct user input."""
    try:
        if not submission_ready(target):
            return "superseded"
        ref = target["ref"]
        failure = target.get("interruption") or target["submission"]
        if reset and not target.get("interruption"):
            proc = run_bb(["thread", "stop", ref, "--json"], timeout=60)
            if proc.returncode:
                return "reset_failed"
            if not submission_ready(target):
                return "superseded"
        proc = run_bb(["thread", "retry", ref, "--turn", failure["request_id"],
                       "--json"], timeout=60)
        if proc.returncode:
            detail = (proc.stdout or "") + (proc.stderr or "")
            if "retry_already_queued" in detail or "no_failed_turn" in detail:
                return "superseded"
            return "command_failed"
        data = json.loads(proc.stdout)
        return data.get("delivery") if data.get("ok") and data.get("delivery") in ("sent", "queued") else "unknown"
    except (BbUnavailable, BbRuntimeError, OSError, subprocess.TimeoutExpired, ValueError, KeyError):
        # A lost reply does not prove that bb rejected the retry. The next scan
        # must reconcile its request/queue before sending anything else.
        return "unknown"


def _resume_args(target):
    observation = target.get("bb_observation")
    ref = target.get("ref")
    if not observation or not isinstance(ref, str) or not ref:
        return None
    shown = _object(bb_json(["thread", "show", ref]))
    current = _object(shown.get("thread"))
    provider = target.get("harness_hint")
    allowed = ("error", "idle") if provider in MESSAGE_ERROR_PROVIDERS else ("error",)
    if (current.get("id") != ref or current.get("providerId") != provider
            or current.get("status") not in allowed or current.get("archivedAt")
            or current.get("deletedAt") or current.get("activeBackgroundAgentCount", 0)
            or current.get("hasPendingInteraction") or target.get("has_pending_interaction")
            or current.get("queuedMessageCount", 0)):
        return None
    if _rows(bb_json(["thread", "queue", "list", ref])):
        return None
    if _rows(bb_json(["thread", "interactions", "list", ref])):
        return None
    error = last_error(_thread_events(ref), provider)
    if not error or _observation(current, error) != observation:
        return None
    if error.get("submission") or error.get("interruption"):
        return None  # These use the separate reserved retry policy.
    if _retry_observation(current, error):
        # bb 0.42 --turn guards the failed request before async dispatch and
        # nudges accepted input. Its guard is not an atomic send transaction.
        return ["thread", "retry", ref, "--turn", error["request_id"], "--json"]
    # Cursor completed/idle errors and legacy logs without request IDs cannot
    # use native retry. tell auto has no atomic expected-request guard;
    # its JSON queued result must be retained if state changes after our reads.
    return ["thread", "tell", ref, RESUME_TEXT, "--mode", "auto", "--json"]


def resume(target):
    """Revalidate the enumerated failure, then report delivery without guessing."""
    if not isinstance(target, dict):
        return "not_sent"
    try:
        args = _resume_args(target)
        if args is None:
            return "superseded"
    except (BbUnavailable, BbRuntimeError, OSError, subprocess.TimeoutExpired, ValueError, TypeError, KeyError):
        return "not_sent"  # All commands so far were read-only.
    try:
        proc = run_bb(args)
    except BbRuntimeError:
        return "not_sent"  # Runtime discovery/start failed before dispatch.
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return "unknown"
    command = getattr(proc, "args", None)
    cmd = command if isinstance(command, (list, tuple)) else args
    log("bb_result", cmd=cmd, code=proc.returncode, stdout=(proc.stdout or "")[-300:])
    if proc.returncode:
        detail = (proc.stdout or "") + (proc.stderr or "")
        if args[1] == "retry" and any(code in detail for code in ("no_failed_turn", "retry_already_queued")):
            return "superseded"
        return "unknown"  # A CLI error can arrive after bb accepted the input.
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return "unknown"
    if isinstance(data, dict) and data.get("ok") is True and data.get("delivery") in ("sent", "queued"):
        return data["delivery"]
    return "unknown"
