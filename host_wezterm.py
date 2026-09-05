"""WezTerm host, driven through `wezterm cli`.

Same contract as host_cmux.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from urllib.parse import unquote, urlparse

import procs
from logbook import log

NAME = "wezterm"
WEZTERM = os.environ.get("WEZTERM_BIN", "wezterm")
PROCESS_NAME = "wezterm-gui"


def _run(args):
    # --no-auto-start: never spawn a mux server when WezTerm is closed.
    # available() must never launch the app (see host_cmux contract).
    cmd = [WEZTERM, "cli", "--no-auto-start", *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log("host_unavailable", host=NAME, error=str(exc))
        return None
    if proc.returncode:
        log("host_command_error", host=NAME, cmd=cmd, code=proc.returncode, stderr=proc.stderr[-300:])
    return proc


def _success(args):
    proc = _run(args)
    return proc is not None and proc.returncode == 0


def available():
    return _success(["list", "--format", "json"])


def _cwd(value):
    parsed = urlparse(value or "")
    path = unquote(parsed.path) if parsed.scheme == "file" else value
    return os.path.realpath(path) if path else path


def _hints(panes):
    counts = {}
    for pane in panes:
        cwd = _cwd(pane.get("cwd"))
        if cwd:
            counts[cwd] = counts.get(cwd, 0) + 1
    roots = procs.app_pids(PROCESS_NAME)
    agents = procs.agent_processes(under_pids=roots) if roots else []
    found = {}
    for agent in agents:
        if counts.get(agent["cwd"]) == 1:
            found.setdefault(agent["cwd"], set()).add(agent["harness"])
    return {cwd: hits.pop() for cwd, hits in found.items() if len(hits) == 1}


def list_targets():
    proc = _run(["list", "--format", "json"])
    if proc is None or proc.returncode:
        return []
    try:
        panes = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        log("host_unavailable", host=NAME, error=str(exc))
        return []
    hints = _hints(panes)
    targets = []
    for pane in panes:
        ref, cwd = str(pane.get("pane_id")), _cwd(pane.get("cwd"))
        targets.append({"ref": ref, "id": ref, "cwd": cwd, "title": pane.get("title"),
                        "harness_hint": hints.get(cwd)})
    return targets


def read_screen(ref):
    proc = _run(["get-text", "--pane-id", str(ref)])
    return proc.stdout if proc is not None and proc.returncode == 0 else ""


def resume(ref):
    def send(text):
        return _success(["send-text", "--pane-id", str(ref), "--no-paste", text])

    ok = send("\x1b")
    time.sleep(0.3)
    ok = send("keep going") and ok
    return send("\r") and ok
