"""bb host adapter implementing the shared host contract via the bb CLI.

Targets are in-scope dead threads and harness_hint is their providerId.
list_targets() fetches and caches each thread's final error.
read_screen() returns the cached detail without another bb call.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone

from immortal.core.common import RESUME_TEXT
from immortal.core.logbook import log

NAME = "bb"
DETECTOR = "bb"

# launchd's PATH has no bb, so default to the app bundle path (like CMUX).
BB = os.environ.get("BB_BIN") or os.environ.get("BB_CLI") or (
    "/Applications/bb.app/Contents/Resources/app.asar.unpacked/"
    "node_modules/bb-app/host-daemon/dist/bb"
)
PROVIDERS = ("claude-code", "codex", "pi", "acp-cursor")
# ADR 0048: Cursor (acp) in bb is in scope, but it never reports a failure
# structurally: no provider/error, turn/completed says "completed" and the
# thread goes idle. The only trace is a final agent message that is just the
# CLI error line ("Error: RetriableError: Connection stalled", thr_vp7hipzyr6,
# 2026-09-03). Recent idle threads of these providers are candidates too.
MESSAGE_ERROR_PROVIDERS = ("acp-cursor",)
IDLE_LOOKBACK_SECS = 24 * 3600
# The bb launcher is `#!/usr/bin/env node`; launchd's PATH lacks Homebrew.
NODE_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")
_ERRORS = {}
# thread id -> (updatedAt, error): `bb thread log` is skipped for unchanged
# threads, so scanning idle candidates every 30s stays cheap.
_LOG_CACHE = {}


def _env():
    env = os.environ.copy()
    env["PATH"] = ":".join((*NODE_DIRS, env.get("PATH", "")))
    return env


class BbUnavailable(Exception):
    """bb binary missing or the bb server is not answering."""


def _ms_to_dt(ms):
    if not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def bb_json(args):
    if shutil.which(BB) is None and not os.path.exists(BB):
        raise BbUnavailable(f"bb binary not found: {BB}")
    proc = subprocess.run(
        [BB, *args, "--json"], capture_output=True, text=True, timeout=30, env=_env()
    )
    if proc.returncode != 0:
        raise BbUnavailable(f"bb {' '.join(args)} failed: {proc.stderr.strip()[-300:]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise BbUnavailable(f"bb {' '.join(args)} returned non-JSON: {exc}")


def available():
    return shutil.which(BB) is not None or os.path.exists(BB)


def _list_error_threads():
    """Threads that may be dead: error status, or recent idle message-error ones."""
    threads = bb_json(["thread", "list"])
    cutoff_ms = (datetime.now(timezone.utc).timestamp() - IDLE_LOOKBACK_SECS) * 1000
    return [
        t
        for t in threads
        if t.get("providerId") in PROVIDERS
        and not t.get("archivedAt")  # bb thread list includes archived threads
        and (
            t.get("status") == "error"
            or (
                t.get("providerId") in MESSAGE_ERROR_PROVIDERS
                and t.get("status") == "idle"
                and (t.get("latestAttentionAt") or 0) >= cutoff_ms
            )
        )
    ]


def _thread_events(thread_id):
    return bb_json(["thread", "log", thread_id, "--all"])


def _message_error(item):
    """A message-provider failure is a final agent message that is only the error line."""
    text = (item.get("text") or "").strip()
    if item.get("type") == "agentMessage" and text.lower().startswith("error:") and "\n" not in text:
        return text
    return None


def last_error(events, provider=None):
    """The final provider/error of the last turn (retry notices are skipped).
    For MESSAGE_ERROR_PROVIDERS the last agent message decides instead."""
    final = None
    for ev in events:
        data = ev.get("data") or {}
        if ev.get("type") == "provider/error":
            if data.get("willRetry"):
                continue
            final = {"detail": data.get("detail") or data.get("message") or "", "at": _ms_to_dt(ev.get("createdAt"))}
        elif provider in MESSAGE_ERROR_PROVIDERS and ev.get("type") == "item/completed":
            item = data.get("item") or {}
            if item.get("type") == "agentMessage":
                text = _message_error(item)
                final = {"detail": text, "at": _ms_to_dt(ev.get("createdAt"))} if text else None
    return final


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
    errors = {}
    try:
        targets = []
        for thread in _list_error_threads():
            ref = thread.get("id")
            error = _final_error(thread)
            if thread.get("status") != "error" and not error:
                continue  # an idle candidate that simply finished its turn
            error_at = error["at"].isoformat() if error and error["at"] else None
            errors[ref] = (error["detail"] if error else None, error_at)
            targets.append({
                "ref": ref,
                "id": ref,
                "cwd": None,
                "title": thread.get("title"),
                "harness_hint": thread.get("providerId"),
                # The detectors' death signal is status == "error". A dead
                # Cursor thread is idle in bb, so the host reports the death.
                "status": "error",
                "bb_status": thread.get("status"),
                "error_at": error_at,
            })
    except (BbUnavailable, subprocess.TimeoutExpired) as exc:
        _ERRORS.clear()
        log("host_unavailable", host=NAME, error=str(exc))
        return []
    _ERRORS.clear()
    _ERRORS.update(errors)
    return targets


def read_screen(ref):
    return _ERRORS.get(ref, (None, None))[0]


def resume(thread_id):
    cmd = [BB, "thread", "tell", thread_id, RESUME_TEXT, "--mode", "auto"]
    proc = subprocess.run(
        # "auto" starts a new turn on an error thread; the default "steer"
        # returns HTTP 409 "Thread is not active" (experiment 0006).
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
        env=_env(),
    )
    log("bb_result", cmd=cmd, code=proc.returncode, stdout=proc.stdout[-300:])
    return proc.returncode == 0
