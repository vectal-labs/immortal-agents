"""Find running Claude Code / Codex processes and the tty they sit on.

Hosts that cannot read pane text (Ghostty) use this to learn which harness a
terminal runs. Hosts that expose a tty per tab (Terminal.app) use it for an
exact tty -> harness mapping."""

from __future__ import annotations

import os
import subprocess

HARNESS_BINARIES = {"claude": "claude", "codex": "codex", "pi": "pi"}
WRAPPERS = {"node", "bun"}


def harness_of(args):
    """Harness name from a process command line, or None.

    Matches only the program being run (first token, or second when launched
    via node/bun), so `rg claude` or a shell with 'codex' in a path is ignored.
    Pi is a node script: also match a path containing pi-coding-agent."""
    tokens = (args or "").split()
    if not tokens:
        return None
    program = os.path.basename(tokens[0])
    script = tokens[1] if program in WRAPPERS and len(tokens) > 1 else tokens[0]
    if "pi-coding-agent" in script:
        return "pi"
    if program in WRAPPERS and len(tokens) > 1:
        program = os.path.basename(tokens[1])
    for harness, binary in HARNESS_BINARIES.items():
        if program == binary:
            return harness
    return None


def process_table():
    out = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid=,tty=,args="], capture_output=True, text=True, timeout=10
    ).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid, ppid, tty, args = parts
        rows.append({"pid": int(pid), "ppid": int(ppid), "tty": tty, "args": args})
    return rows


def cwd_of(pid):
    out = subprocess.run(
        ["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout
    for line in out.splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def has_ancestor(pid, ancestors, table):
    """True if any pid in `ancestors` is above `pid` in the process tree."""
    parents = {row["pid"]: row["ppid"] for row in table}
    seen = set()
    while pid and pid not in seen:
        seen.add(pid)
        if pid in ancestors:
            return True
        pid = parents.get(pid)
    return False


def agent_processes(table=None, under_pids=None):
    """Running harness processes: [{pid, ppid, tty, args, harness, cwd}].

    `under_pids` limits the result to descendants of those pids (an app)."""
    table = table if table is not None else process_table()
    found = []
    for row in table:
        harness = harness_of(row["args"])
        if not harness:
            continue
        if under_pids and not has_ancestor(row["pid"], set(under_pids), table):
            continue
        found.append({**row, "harness": harness, "cwd": cwd_of(row["pid"])})
    return found


def app_pids(process_name):
    out = subprocess.run(["/usr/bin/pgrep", "-x", process_name], capture_output=True, text=True, timeout=5).stdout
    return [int(p) for p in out.split()]
