"""Kitty host, driven through its remote-control socket.

Same contract as host_cmux. Set KITTY_LISTEN_ON for background use.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import time

import procs
from logbook import log

NAME = "kitty"
KITTY = os.environ.get("KITTY_BIN", "kitty")
KITTY_LISTEN_ON = os.environ.get("KITTY_LISTEN_ON")


def _socket():
    # kitty.conf listen_on appends -<pid> to the socket path.
    # CLI --listen-on uses the exact path. Accept both.
    if not KITTY_LISTEN_ON or not KITTY_LISTEN_ON.startswith("unix:"):
        return KITTY_LISTEN_ON
    base = KITTY_LISTEN_ON[5:]
    if os.path.exists(base):
        return KITTY_LISTEN_ON
    matches = sorted(glob.glob(base + "-*"))
    return ("unix:" + matches[-1]) if matches else KITTY_LISTEN_ON


def _run(args):
    target = _socket()
    cmd = [KITTY, "@", *(["--to", target] if target else []), *args]
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
    return _success(["ls"])


def _hint(pid, agents, table):
    hits = {a["harness"] for a in agents if pid and procs.has_ancestor(a["pid"], {pid}, table)}
    return hits.pop() if len(hits) == 1 else None


def list_targets():
    proc = _run(["ls"])
    if proc is None or proc.returncode:
        return []
    try:
        listing = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        log("host_unavailable", host=NAME, error=str(exc))
        return []
    table = procs.process_table()
    agents = procs.agent_processes(table)
    targets = []
    for os_window in listing:
        for tab in os_window.get("tabs", []):
            for window in tab.get("windows", []):
                ref = str(window.get("id"))
                targets.append({"ref": ref, "id": ref, "cwd": window.get("cwd"),
                                "title": window.get("title"),
                                "harness_hint": _hint(window.get("pid"), agents, table)})
    return targets


def read_screen(ref):
    proc = _run(["get-text", "--match", f"id:{ref}", "--extent", "screen"])
    return proc.stdout if proc is not None and proc.returncode == 0 else ""


def resume(ref):
    match = ["--match", f"id:{ref}"]
    ok = _success(["send-key", *match, "escape"])
    time.sleep(0.3)
    ok = _success(["send-text", *match, "keep going"]) and ok
    return _success(["send-key", *match, "enter"]) and ok
