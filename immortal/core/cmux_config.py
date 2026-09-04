"""Keep cmux's socket control mode at "automation" via ~/.config/cmux/cmux.json.

The launchd watcher is not "started inside cmux", so cmux's default cmuxOnly
mode refuses it with "Access denied" and the watcher sees zero cmux panes. A
value in cmux.json is file-managed: it overrides the Settings UI and survives
cmux settings migrations, which is what flipped a tester back to cmuxOnly.

Usage (from install.sh): python3 -m core.cmux_config get|ensure
  get     prints the effective mode (file value, else the prefs plist, else cmuxOnly)
  ensure  edits cmux.json in place if needed; prints unchanged | written | error: ...
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time

CMUX_JSON = os.path.expanduser("~/.config/cmux/cmux.json")
WANT = "automation"
KEY_RE = re.compile(r'^(.*?"socketControlMode"\s*:\s*")([^"]*)(".*)$')
BLOCK_RE = re.compile(r'^\s*"automation"\s*:\s*\{')
EMPTY_BLOCK_RE = re.compile(r'^(\s*"automation"\s*:\s*)\{\s*\}(.*)$')
COMMENT = "  // immortal-agents: the launchd watcher must reach the cmux socket (cmuxOnly blocks it)."
BLOCK = '  "automation": { "socketControlMode": "%s" },' % WANT
NEW_FILE = "{\n%s\n%s\n}\n" % (COMMENT, BLOCK.rstrip(","))


def _active(line):
    return not line.strip().startswith("//")


def file_mode(text):
    """Mode set in cmux.json, or None when the file does not manage it."""
    for line in text.splitlines():
        m = KEY_RE.match(line)
        if m and _active(line):
            return m.group(2)
    return None


def prefs_mode():
    proc = subprocess.run(
        ["/usr/bin/defaults", "read", "com.cmuxterm.app", "socketControlMode"],
        capture_output=True, text=True, timeout=10,
    )
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else "cmuxOnly"


def effective_mode(text=None):
    if text is None:
        text = open(CMUX_JSON).read() if os.path.exists(CMUX_JSON) else ""
    return file_mode(text) or prefs_mode()


def patched(text):
    """Return text with socketControlMode forced to automation, or None if already so."""
    if not text.strip():
        return NEW_FILE
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        m = KEY_RE.match(line)
        if m and _active(line):
            if m.group(2) == WANT:
                return None
            lines[i] = m.group(1) + WANT + m.group(3) + ("\n" if line.endswith("\n") else "")
            return "".join(lines)
    for i, line in enumerate(lines):
        if not _active(line):
            continue
        m = EMPTY_BLOCK_RE.match(line)
        if m:
            lines[i] = '%s{ "socketControlMode": "%s" }%s\n' % (m.group(1), WANT, m.group(2).rstrip("\n"))
            return "".join(lines)
        if BLOCK_RE.match(line):
            nxt = next((l for l in lines[i + 1:] if l.strip() and _active(l)), "}")
            comma = "" if nxt.strip().startswith("}") else ","
            lines.insert(i + 1, '    "socketControlMode": "%s"%s\n' % (WANT, comma))
            return "".join(lines)
    for i, line in enumerate(lines):
        if line.strip().startswith("{"):
            lines.insert(i + 1, COMMENT + "\n" + BLOCK + "\n")
            return "".join(lines)
    return None


def ensure(path=CMUX_JSON):
    text = open(path).read() if os.path.exists(path) else ""
    new = patched(text)
    if new is None:
        return "unchanged" if file_mode(text) == WANT else "error: could not find where to put socketControlMode in " + path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if text:
        backup = "%s.%s.bak" % (path, time.strftime("%Y%m%d-%H%M%S"))
        with open(backup, "w") as fh:
            fh.write(text)
    with open(path, "w") as fh:
        fh.write(new)
    return "written"


if __name__ == "__main__":
    verb = sys.argv[1] if len(sys.argv) > 1 else "get"
    print(effective_mode() if verb == "get" else ensure())
