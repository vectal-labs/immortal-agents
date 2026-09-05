"""cmux host: the cmux CLI reads pane text and sends keys over its socket.

Host contract (shared by host_terminal / host_ghostty):
    NAME            short host name used in logs and Discord
    available()     app is running; must never launch it
    list_targets()  [{"ref", "id", "cwd", "title", "harness_hint"}]
    read_screen(ref) -> visible pane text, or None if the host cannot expose it
    resume(ref)     -> True when ESC + "keep going" + Enter was delivered
"""

from __future__ import annotations

import json
import os
import subprocess
import time

from logbook import log

NAME = "cmux"
CMUX = os.environ.get("CMUX_BIN", "/Applications/cmux.app/Contents/Resources/bin/cmux")


def _env():
    env = os.environ.copy()
    env["CMUX_QUIET"] = "1"
    return env


def cmux_json(args):
    cmd = [CMUX, "--json", *args]
    log("cmux_exec", cmd=cmd)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20, env=_env())
    if proc.returncode != 0:
        log("cmux_error", cmd=cmd, stderr=proc.stderr[-500:], code=proc.returncode)
        return None
    return json.loads(proc.stdout)


def cmux_run(args):
    cmd = [CMUX, *args]
    log("cmux_exec", cmd=cmd)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20, env=_env())
    log("cmux_result", cmd=cmd, code=proc.returncode, stdout=proc.stdout[-300:])
    return proc.returncode == 0


def available():
    return os.path.exists(CMUX)


def list_targets():
    listing = cmux_json(["workspace", "list"])
    tree = cmux_json(["tree", "--id-format", "both"])
    if not listing or not tree:
        return []
    cwd_by_ref = {w.get("ref"): w.get("current_directory") for w in listing.get("workspaces", [])}
    targets = []
    for window in tree.get("windows", []):
        for ws in window.get("workspaces", []):
            cwd = cwd_by_ref.get(ws.get("ref"))
            needle = os.environ.get("WATCHER_CWD_CONTAINS")
            if needle and needle not in (cwd or ""):
                continue
            for pane in ws.get("panes", []):
                for surf in pane.get("surfaces", []):
                    if surf.get("type") != "terminal":
                        continue
                    targets.append(
                        {
                            "workspace_ref": ws.get("ref"),
                            "ref": surf.get("ref"),
                            "id": surf.get("id"),
                            "cwd": cwd,
                            "title": surf.get("title"),
                            "harness_hint": None,
                        }
                    )
    return targets


def read_screen(ref):
    proc = subprocess.run(
        [CMUX, "read-screen", "--surface", ref],
        capture_output=True,
        text=True,
        timeout=20,
        env=_env(),
    )
    screen = proc.stdout or ""
    log("pane_read", surface=ref, code=proc.returncode, screen_tail=screen[-800:])
    return screen


def resume(ref):
    ok = cmux_run(["send-key", "--surface", ref, "esc"])
    time.sleep(0.3)
    ok = cmux_run(["send", "--surface", ref, "keep going"]) and ok
    ok = cmux_run(["send-key", "--surface", ref, "enter"]) and ok
    return ok
