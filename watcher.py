#!/usr/bin/env python3
"""Ever-present internet watcher. Quiet to the user; verbose to the log file."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone

import detect as claude_detect
import detect_codex as codex_detect
import detect_pi as pi_detect
import host_bb
import host_cmux
import host_ghostty
import host_terminal
import notify
import ready
from logbook import STATE_DIR, log, now_iso

PROBE_URL = "http://captive.apple.com/hotspot-detect.html"
PROBE_SECS = 10
MIN_OUTAGE_SECS = 120
# Experiment 0009: a revive can die again (stale DNS). Re-check the revived
# targets for a while after a revive and revive again, a bounded number of times.
MAX_REVIVES = 3
RECHECK_SECS = 60
RECHECK_WINDOW_SECS = 300
# Provider-side outages while online (Grok "at capacity", 2026-09-03): poll bb
# every 30s. A thread gets at most MAX_REVIVES such revives per hour.
PROVIDER_CHECK_SECS = 30
PROVIDER_EPISODE_SECS = 3600
STATE_PATH = STATE_DIR / "state.json"
LOCK_PATH = STATE_DIR / "watcher.lock"
SIMULATED_OUTAGE_PATH = STATE_DIR / "simulated_outage.json"
# Claude Code TUI markers (footer); Codex markers live in detect_codex.
CLAUDE_PANE_MARKERS = ("shift+tab to cycle", "⏵⏵")
# Terminal-style hosts share one contract (see host_cmux docstring).
# bb is different (structured thread state) and has its own path.
TERMINAL_HOSTS = (host_cmux, host_terminal, host_ghostty)


def load_state():
    if not STATE_PATH.exists():
        return {"online": None, "outage_started_at": None, "resumed": {}}
    return json.loads(STATE_PATH.read_text())


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def simulated_outage_active():
    if not SIMULATED_OUTAGE_PATH.exists():
        return False
    try:
        flag = json.loads(SIMULATED_OUTAGE_PATH.read_text())
        expires_at = datetime.fromisoformat(flag["expires_at"].replace("Z", "+00:00"))
    except FileNotFoundError:
        return False
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        log("sim_invalid", error=str(exc))
        return False
    if expires_at <= datetime.now(timezone.utc):
        SIMULATED_OUTAGE_PATH.unlink(missing_ok=True)
        log("sim_expired", expires_at=flag["expires_at"])
        return False
    return flag.get("offline") is True


def probe():
    if simulated_outage_active():
        log("probe", online=False, simulated=True)
        return False
    try:
        req = urllib.request.Request(PROBE_URL, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read(256).decode("utf-8", "replace")
            ok = resp.status == 200 and "Success" in body
            log("probe", online=ok, http_status=resp.status, body=body[:80])
            return ok
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log("probe", online=False, error=str(exc))
        return False


def detect_harness(screen, title, hint=None):
    """Identify the harness: pane text first (ADR 0033), then the host's
    process hint, then the title."""
    flat = codex_detect.normalize(screen)
    looks_claude = any(marker in flat for marker in CLAUDE_PANE_MARKERS)
    looks_codex = codex_detect.is_codex_pane(screen or "")
    looks_pi = pi_detect.is_pi_pane(screen or "")
    hits = [name for name, ok in (("claude", looks_claude), ("codex", looks_codex), ("pi", looks_pi)) if ok]
    if len(hits) == 1:
        return hits[0], "pane_text"
    if len(hits) > 1:
        return None, "ambiguous_pane_text"
    if hint in ("claude", "codex", "pi"):
        return hint, "process"
    lowered = (title or "").lower()
    titled = [name for name in ("claude", "codex") if name in lowered]
    raw_title = title or ""
    if pi_detect.is_pi_pane(raw_title) or raw_title.strip().lower() in ("π", "pi"):
        titled.append("pi")
    if len(titled) == 1:
        return titled[0], "surface_title"
    if len(titled) > 1:
        return None, "ambiguous_pane_text"
    return None, "unknown_harness"


def on_recovery(state, loss_at, recovery_at, duration):
    log("recovery", loss_at=loss_at, recovery_at=recovery_at, duration_secs=duration)
    if duration < MIN_OUTAGE_SECS:
        log("skip_recovery", reason="outage_too_short", duration_secs=duration)
        return
    ready.wait_for_apis()
    # Experiment 0013: the captive probe passed 101s before api.anthropic.com
    # resolved, and three bb threads died in that gap. For the agents the
    # outage ends when their APIs resolve, so that is the window end every
    # detector gets. The probe time stays in the log and in the duration.
    api_ready_at = now_iso()
    state["last_api_ready_at"] = api_ready_at
    log("outage_window", loss_at=loss_at, probe_recovery_at=recovery_at, api_ready_at=api_ready_at)
    if revive_pass(state, loss_at, api_ready_at, duration):
        arm_recheck(state, loss_at, duration)


def revive_pass(state, loss_at, recovery_at, duration, recheck=False):
    """One pass over every host. Returns how many revives were sent."""
    sent = 0
    for host in TERMINAL_HOSTS:
        if not host.available():
            log("host_skipped", host=host.NAME, reason="not_running")
            continue
        sent += recover_terminal_host(host, state, loss_at, recovery_at, duration, recheck)
    sent += recover_bb_threads(state, loss_at, recovery_at, duration, recheck)
    return sent


def may_revive(resumed, key, recheck):
    """First pass: only targets never revived (once per outage). Recheck: only
    targets we already revived, under the cap. A recheck never wakes new ones."""
    tries = resumed.get(key, {}).get("tries", 0)
    if not recheck:
        return tries == 0
    return 0 < tries < MAX_REVIVES


def mark_revived(state, key, **fields):
    entry = state["resumed"].get(key, {})
    state["resumed"][key] = {**fields, "tries": entry.get("tries", 0) + 1}
    save_state(state)


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
    if now < parse_iso(rc["next_at"]):
        return
    if now > parse_iso(rc["until"]):
        state["recheck"] = None
        log("recheck_done", loss_at=rc["loss_at"])
        return
    log("recheck", loss_at=rc["loss_at"])
    ready.wait_for_apis()
    if revive_pass(state, rc["loss_at"], now_iso(), rc["duration"], recheck=True):
        arm_recheck(state, rc["loss_at"], rc["duration"])
    else:
        rc["next_at"] = iso_after(now, RECHECK_SECS)


def parse_iso(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def iso_after(dt, secs):
    return datetime.fromtimestamp(dt.timestamp() + secs, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def recover_terminal_host(host, state, loss_at, recovery_at, duration, recheck=False):
    """One pass over a terminal-style host: read each pane, decide, revive once."""
    resumed = state.setdefault("resumed", {})
    sent = 0
    targets = host.list_targets()
    log("targets_enumerated", host=host.NAME, count=len(targets), targets=targets)
    for target in targets:
        ref = target["ref"]
        cwd = target.get("cwd")
        screen = host.read_screen(ref)
        harness, harness_reason = detect_harness(screen, target.get("title"), target.get("harness_hint"))
        log("harness_detected", host=host.NAME, ref=ref, title=target.get("title"), harness=harness, reason=harness_reason)
        if not harness:
            log("decision", host=host.NAME, ref=ref, harness=None, decision="skip", reasons=[harness_reason])
            continue

        if harness == "claude":
            jsonl = claude_detect.find_jsonl(cwd, target.get("id"))
            if not jsonl:
                log("no_session_mapping", host=host.NAME, ref=ref, cwd=cwd,
                    candidate_count=len(claude_detect.jsonl_candidates(cwd)))
            jsonl_info = claude_detect.summarize_jsonl(jsonl) if jsonl else None
            log("jsonl_eval", host=host.NAME, ref=ref, harness=harness, cwd=cwd, info=jsonl_info)
            pane_class = claude_detect.UNREADABLE if screen is None else claude_detect.classify_pane(screen)
            decision, reasons = claude_detect.evaluate(jsonl_info, pane_class, loss_at)
            key = f"{jsonl_info['path'] if jsonl_info else host.NAME + ':' + ref}:{loss_at}"
        elif harness == "pi":
            pane_class = pi_detect.UNREADABLE if screen is None else pi_detect.classify_pane(screen)
            decision, reasons, info = pi_detect.evaluate(cwd, pane_class, loss_at, recovery_at)
            log("jsonl_eval", host=host.NAME, ref=ref, harness=harness, cwd=cwd, info=info)
            key = f"{host.NAME}:{ref}:{loss_at}"
        else:
            # ADR 0033: no pane→rollout mapping; a task_complete inside the
            # outage window plus the pane fingerprint is the death signal.
            pane_class = codex_detect.UNREADABLE if screen is None else codex_detect.classify_pane(screen)
            decision, reasons, info = codex_detect.evaluate(cwd, pane_class, loss_at, recovery_at)
            log("jsonl_eval", host=host.NAME, ref=ref, harness=harness, cwd=cwd, info=info)
            key = f"{host.NAME}:{ref}:{loss_at}"

        log("decision", host=host.NAME, ref=ref, harness=harness, cwd=cwd, decision=decision,
            reasons=reasons, tries=resumed.get(key, {}).get("tries", 0), recheck=recheck)
        # ADR 0019: observe only — never resume a suspected silent hang
        if "silent_hang_observed" in reasons:
            log("silent_hang_observed", host=host.NAME, ref=ref, cwd=cwd, acted=False, reasons=reasons)
        if decision != "resume" or not may_revive(resumed, key, recheck):
            continue
        ok = host.resume(ref)
        log("resume_sent", host=host.NAME, ref=ref, ok=ok)
        announce(host.NAME, harness, duration, target.get("title") or cwd, ok)
        if ok:
            sent += 1
            mark_revived(state, key, at=recovery_at, host=host.NAME, ref=ref, harness=harness)
    return sent


def announce(host, harness, offline_secs, label, ok):
    """Discord ping per revive attempt. Failure to notify never blocks recovery."""
    sent = notify.notify_revive(host, harness, offline_secs, label, ok)
    log("notify", host=host, harness=harness, ok=ok, sent=sent)


def recover_bb_threads(state, loss_at, recovery_at, duration=0, recheck=False):
    """ADR 0036: bb is a second host. Dead threads are found via the bb CLI."""
    resumed = state.setdefault("resumed", {})
    sent = 0
    try:
        threads = host_bb.list_error_threads()
    except (host_bb.BbUnavailable, subprocess.TimeoutExpired) as exc:
        log("bb_unavailable", error=str(exc))
        return 0
    log("bb_threads_enumerated", count=len(threads), ids=[t.get("id") for t in threads])
    for thread in threads:
        thread_id = thread.get("id")
        try:
            events = host_bb.thread_events(thread_id)
        except (host_bb.BbUnavailable, subprocess.TimeoutExpired) as exc:
            log("bb_unavailable", thread=thread_id, error=str(exc))
            continue
        decision, reasons, info = host_bb.evaluate(thread, events, loss_at, recovery_at)
        key = f"bb:{thread_id}:{loss_at}"
        log("bb_eval", thread=thread_id, info=info)
        log(
            "decision",
            host="bb",
            thread=thread_id,
            harness=thread.get("providerId"),
            title=thread.get("title"),
            decision=decision,
            reasons=reasons,
            tries=resumed.get(key, {}).get("tries", 0),
            recheck=recheck,
        )
        if decision != "resume" or not may_revive(resumed, key, recheck):
            continue
        ok, output = host_bb.resume(thread_id)
        log("resume_sent", host="bb", thread=thread_id, ok=ok, output=output)
        announce("bb", thread.get("providerId"), duration, thread.get("title") or thread_id, ok)
        if ok:
            sent += 1
            mark_revived(state, key, at=recovery_at, thread=thread_id, harness=thread.get("providerId"), host="bb")
    return sent


def run_provider_check(state):
    """Every online tick: revive bb threads that a provider rejected while the
    Mac was online. Whitelisted phrases only; anything else is announced once."""
    now = datetime.now(timezone.utc)
    next_at = state.get("provider_check_next_at")
    if next_at and now < parse_iso(next_at):
        return
    state["provider_check_next_at"] = iso_after(now, PROVIDER_CHECK_SECS)
    try:
        threads = host_bb.list_error_threads()
    except (host_bb.BbUnavailable, subprocess.TimeoutExpired) as exc:
        log("bb_unavailable", error=str(exc))
        return
    for thread in threads:
        thread_id = thread.get("id")
        try:
            events = host_bb.thread_events(thread_id)
        except (host_bb.BbUnavailable, subprocess.TimeoutExpired) as exc:
            log("bb_unavailable", thread=thread_id, error=str(exc))
            continue
        decision, reasons, info = host_bb.evaluate_provider_outage(thread, events, now)
        entry = state.setdefault("provider_revived", {}).get(thread_id, {})
        if info["error_at"] == entry.get("error_at"):
            continue  # this exact failure was already handled
        log("provider_decision", thread=thread_id, harness=thread.get("providerId"),
            title=thread.get("title"), decision=decision, reasons=reasons, info=info, tries=entry.get("tries", 0))
        label = thread.get("title") or thread_id
        if decision == "unknown":
            state["provider_revived"][thread_id] = {**entry, "error_at": info["error_at"]}
            notify.send(f'Unhandled provider error: {thread.get("providerId")} in bb · "{label}" · {info["error_detail"][:200]}')
            continue
        if decision != "resume":
            continue
        tries = entry.get("tries", 0)
        if entry.get("at") and (now - parse_iso(entry["at"])).total_seconds() > PROVIDER_EPISODE_SECS:
            tries = 0
        if tries >= MAX_REVIVES:
            log("provider_revive_capped", thread=thread_id, tries=tries)
            state["provider_revived"][thread_id] = {**entry, "error_at": info["error_at"]}
            continue
        ok, output = host_bb.resume(thread_id)
        log("resume_sent", host="bb", thread=thread_id, ok=ok, output=output, trigger="provider_outage")
        status = "Revived" if ok else "Revive FAILED"
        sent = notify.send(f'{status}: {thread.get("providerId")} in bb · provider outage · "{label}" · {info["error_detail"][:120]}')
        log("notify", host="bb", harness=thread.get("providerId"), ok=ok, sent=sent)
        state["provider_revived"][thread_id] = {"error_at": info["error_at"], "at": now_iso(), "tries": tries + 1}
        save_state(state)


def acquire_single_instance_lock():
    """ADR 0013: exactly one watcher; a second copy logs and exits nonzero."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fh = LOCK_PATH.open("w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("already_running", lock=str(LOCK_PATH))
        sys.exit(1)
    return fh


def loop():
    log("start", probe_secs=PROBE_SECS, min_outage_secs=MIN_OUTAGE_SECS, pid=os.getpid())
    state = load_state()
    while True:
        try:
            online = probe()
            prev = state.get("online")
            if online is False and prev is not False:
                if not state.get("outage_started_at"):
                    state["outage_started_at"] = now_iso()
                    state["last_loss_at"] = state["outage_started_at"]
                    log("state_change", change="to_offline", at=state["outage_started_at"], prev=prev)
            elif prev is False and online is True:
                recovery = now_iso()
                loss = state.get("outage_started_at")
                duration = 0
                if loss:
                    start = datetime.fromisoformat(loss.replace("Z", "+00:00"))
                    duration = (datetime.now(timezone.utc) - start).total_seconds()
                state["last_recovery_at"] = recovery
                state["outage_started_at"] = None
                log("state_change", change="offline_to_online", at=recovery, duration_secs=duration)
                on_recovery(state, loss, recovery, duration)
            elif online:
                run_recheck(state)
                run_provider_check(state)
            state["online"] = online
            state["last_probe_at"] = now_iso()
            save_state(state)
            if os.environ.get("WATCHER_ONCE"):
                log("once_exit")
                return
        except Exception as exc:
            log("loop_error", error=str(exc), tb=traceback.format_exc()[-800:])
        time.sleep(PROBE_SECS)


if __name__ == "__main__":
    try:
        lock_fh = acquire_single_instance_lock()
        log("alive", pid=os.getpid(), ppid=os.getppid())
        loop()
    except Exception as exc:
        log("fatal", error=str(exc), tb=traceback.format_exc()[-800:])
        sys.stderr.write(traceback.format_exc())
        raise
