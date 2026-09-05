"""Ghostty host, driven over AppleScript (JXA, Ghostty >= 1.3).

Ghostty can receive keys (`send key`, `input text`) but exposes no screen
text: a terminal has only `name` (title) and `working directory`. So
read_screen returns None and the decision rests on the harness log alone
(pane class "unreadable" in detect_claude / detect_codex). The harness is learned
from the title or from a claude/codex process under Ghostty with the same cwd.

It implements the host contract documented in host_cmux."""

from __future__ import annotations

import os
import subprocess

from immortal.core import osa
from immortal.core import procs
from immortal.core.common import RESUME_TEXT
from immortal.core.logbook import log

NAME = "ghostty"
DETECTOR = None
PROCESS_NAME = "ghostty"
LIST_JS = """
const G = Application("Ghostty");
const out = [];
G.windows().forEach(w => w.tabs().forEach(t => t.terminals().forEach(term => out.push({
  id: term.id(), title: term.name(), cwd: term.workingDirectory(), window: w.id(), tab: t.id()
}))));
JSON.stringify(out);
"""
RESUME_JS = """
const G = Application("Ghostty");
const term = G.terminals().find(t => t.id() === %(id)s);
if (!term) throw new Error("terminal gone: " + %(id)s);
G.sendKey("escape", {to: term});
delay(0.3);
G.inputText(%(text)s, {to: term});
G.sendKey("enter", {to: term});
'ok';
"""


def available():
    return osa.app_running(PROCESS_NAME)


def _real(path):
    # Ghostty reports /tmp/x; lsof reports /private/tmp/x. Compare resolved paths.
    return os.path.realpath(path) if path else None


def _hint_by_cwd(terminals):
    """resolved cwd -> harness for cwds that map to exactly one Ghostty terminal."""
    counts = {}
    for term in terminals:
        key = _real(term.get("cwd"))
        counts[key] = counts.get(key, 0) + 1
    hints = {}
    for agent in procs.agent_processes(under_pids=procs.app_pids(PROCESS_NAME)):
        key = _real(agent["cwd"])
        if counts.get(key) == 1:
            hints[key] = agent["harness"]
    return hints


def list_targets():
    try:
        terminals = osa.run_jxa_json(LIST_JS) or []
    except (osa.OsaError, subprocess.TimeoutExpired) as exc:
        log("host_unavailable", host=NAME, error=str(exc))
        return []
    hints = _hint_by_cwd(terminals)
    return [
        {
            "ref": term.get("id"),
            "id": term.get("id"),
            "cwd": term.get("cwd"),
            "title": term.get("title"),
            "harness_hint": hints.get(_real(term.get("cwd"))),
        }
        for term in terminals
    ]


def read_screen(ref):
    return None


def resume(target):
    ref = target["ref"]
    try:
        osa.run_jxa(RESUME_JS % {"id": osa.js(ref), "text": osa.js(RESUME_TEXT)})
    except (osa.OsaError, subprocess.TimeoutExpired) as exc:
        log("resume_error", host=NAME, ref=ref, error=str(exc))
        return "unknown"
    except (FileNotFoundError, PermissionError):
        return "not_sent"
    except OSError:
        return "unknown"
    return "sent"
