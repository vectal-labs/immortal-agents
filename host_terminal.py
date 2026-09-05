"""macOS Terminal.app host, driven over AppleScript (JXA).

Each tab exposes `contents` (visible text), `tty`, and `processes`, so this
host reads the screen like cmux. Input goes through `do script`, which types
the text and presses Return. Terminal.app has no raw-key command, so no ESC
is sent; both harnesses sit idle at their prompt after a network death.

Same contract as host_cmux."""

from __future__ import annotations

import os
import subprocess

import osa
import procs
from logbook import log

NAME = "terminal"
PROCESS_NAME = "Terminal"
RESUME_TEXT = "keep going"

LIST_JS = """
const T = Application("Terminal");
const out = [];
T.windows().forEach(w => w.tabs().forEach((t, i) => out.push({
  tty: t.tty(), title: t.customTitle(), processes: t.processes(), window: w.id(), index: i + 1
})));
JSON.stringify(out);
"""
TAB_JS = """
const T = Application("Terminal");
const tab = T.windows().flatMap(w => w.tabs()).find(t => t.tty() === %(tty)s);
if (!tab) throw new Error("tab gone: " + %(tty)s);
"""
READ_JS = TAB_JS + "JSON.stringify(tab.contents());"
RESUME_JS = TAB_JS + "T.doScript(%(text)s, {in: tab}); 'ok';"


def available():
    return osa.app_running(PROCESS_NAME)


def _tty_name(tty):
    return os.path.basename(tty or "")


def list_targets():
    try:
        tabs = osa.run_jxa_json(LIST_JS) or []
    except (osa.OsaError, subprocess.TimeoutExpired) as exc:
        log("host_unavailable", host=NAME, error=str(exc))
        return []
    agents = {_tty_name(a["tty"]): a for a in procs.agent_processes()}
    targets = []
    for tab in tabs:
        tty = tab.get("tty")
        agent = agents.get(_tty_name(tty))
        hint = agent["harness"] if agent else next(
            (h for h in map(procs.harness_of, tab.get("processes") or []) if h), None
        )
        targets.append(
            {
                "ref": tty,
                "id": tty,
                "cwd": agent["cwd"] if agent else None,
                "title": tab.get("title"),
                "harness_hint": hint,
            }
        )
    return targets


def read_screen(ref):
    try:
        screen = osa.run_jxa_json(READ_JS % {"tty": osa.js(ref)}) or ""
    except (osa.OsaError, subprocess.TimeoutExpired) as exc:
        log("pane_read_error", host=NAME, ref=ref, error=str(exc))
        return ""
    log("pane_read", host=NAME, ref=ref, screen_tail=screen[-800:])
    return screen


def resume(ref):
    try:
        osa.run_jxa(RESUME_JS % {"tty": osa.js(ref), "text": osa.js(RESUME_TEXT)})
    except (osa.OsaError, subprocess.TimeoutExpired) as exc:
        log("resume_error", host=NAME, ref=ref, error=str(exc))
        return False
    return True
