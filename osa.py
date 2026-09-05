"""Run JavaScript-for-Automation (JXA) and get JSON back.

Shared by the AppleScript-driven hosts (Terminal.app, Ghostty). JXA is used
instead of AppleScript because it can JSON.stringify its result."""

from __future__ import annotations

import json
import subprocess


class OsaError(Exception):
    """osascript failed. Includes macOS Automation (TCC) denials, error -1743."""


def app_running(process_name):
    """True if the app is running. Never launches it: `tell application` would."""
    proc = subprocess.run(["/usr/bin/pgrep", "-x", process_name], capture_output=True, timeout=5)
    return proc.returncode == 0


def run_jxa(script, timeout=20):
    proc = subprocess.run(
        ["/usr/bin/osascript", "-l", "JavaScript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise OsaError(proc.stderr.strip()[-300:])
    return proc.stdout.strip()


def run_jxa_json(script, timeout=20):
    out = run_jxa(script, timeout)
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise OsaError(f"non-JSON from osascript: {out[:200]}") from exc


def js(value):
    """Embed a Python value into a JXA script as a JS literal."""
    return json.dumps(value)
